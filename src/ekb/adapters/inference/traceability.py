from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
from pydantic import Field, ValidationError

from ekb.adapters.inference import OpenAIInference
from ekb.adapters.retry import retry
from ekb.config import Settings
from ekb.domain.models import Model, Usage, fingerprint, total_usage
from ekb.domain.traceability import (
    ParentSelection,
    TraceabilityAssertion,
    TraceabilitySelection,
    TraceabilityVerification,
)
from ekb.observability import tracer

INSTRUCTIONS = (
    Path(__file__)
    .with_name("skills")
    .joinpath("control_traceability.md")
    .read_text(encoding="utf-8")
)
ASSESSMENT_BATCH_SIZE = 10
ARBITRATION_INSTRUCTIONS = """
You are the final traceability arbiter. The supplied candidates already passed independent
semantic assessment. Select only the smallest set of closest, most specific parents needed to
explain the child. A broad framework outcome, neighboring capability, or generic partial clause is
not a parent when a more direct requirement captures the stated behavior. Candidate confidence is
advisory and is not comparable across assessment batches. Reapply the strict textual-entailment
test: the child's words must state the selected atomic parent clause's action and object or outcome,
not merely something that could contribute to it. Do not require identical wording or every
conjunct in a compound parent: an independently meaningful clause is enough. Generic mechanism
terms retain their stated meaning after provider names are removed: explicit segmentation,
restriction, filtering, encryption, monitoring, detection, secure connectivity, access control,
or protection can make the corresponding broader behavior operational. Product names without
such stated behavior remain insufficient. Do not reduce a broad governance process such as risk
assessment, incident governance, or configuration management to an incidental technical action.
However, retain an independently meaningful technical clause inside a compound requirement:
accepting a connection can authorize that connection, and explicit effectiveness monitoring can
implement an effectiveness-monitoring clause without satisfying the surrounding process. A
restrictive/common secure configuration-settings clause retains those qualifiers; an arbitrary
setting or operating mode is insufficient without hardening, baseline, consistency, restrictive,
common-secure, or equivalent security-configuration context. Prefer the candidate with the
fewest unstated actors, locations, mechanisms, and outcomes. Generating an alert does not alert
specified personnel; a firewall does not imply a managed external interface; a private connection
does not imply segmentation; monitoring resilience does not implement a resilience mechanism; and
one protection outcome does not establish every confidentiality, integrity, and availability
outcome. Conversely, an explicit statement that network controls secure or protect services can
specialize a network-protection outcome, and controlling which networks may access a service can
specialize access authorization without requiring the child to repeat the word "authorized."
Disabling public network access directly specializes prohibition of a public-network connection.
A child that configures monitoring specifically to validate protection effectiveness directly
implements effectiveness monitoring; prefer that exact clause over generic control assessment or
resilience mechanisms.
Prefer a firewall explicitly deployed at a network edge or for external-network filtering as an
edge policy-enforcement or external-interface control, not merely a broad network-protection
outcome. Private Link, a private endpoint, or a private access point described as secure private
service access can implement network separation. A secure VPN connection, especially when IPsec is
stated, can implement protected transmission or cryptographic remote-session protection. Static
application security testing and vulnerability management before deployment can implement static
analysis, vulnerability scanning, and secure-development integration. Detection-signature tuning
for fidelity can implement attack-detection monitoring and monitoring-result accuracy.
When accepted candidates include a direct action, mechanism, or outcome match, select the closest
one or more rather than returning no link. Return no link only when every accepted candidate still
depends on unstated capabilities or hoped-for effects. Use only supplied IDs and copy evidence
excerpts verbatim. Return JSON matching the schema.
"""
CRITIC_INSTRUCTIONS = """
Act as an adversarial final reviewer of links selected by another mapper. Judge every supplied link
independently and keep it only when the child text explicitly supports a defensible atomic parent
clause. Reject a link that relies on a product-category name alone, treats a contributing control
as proof of its hoped-for outcome, or drops a defining parent actor, location, mechanism, scope, or
combined outcome. In particular: attack protection is not automatically resilience; IDS/IPS,
firewall-rule, VPN, private-connection, and access-control names do not supply unstated monitoring,
enforcement, remote-access management, segmentation, authorized actors, or risk context; metadata
requirements are not secure component settings; and generic protection does not establish every
confidentiality, integrity, and availability outcome. Do not reject merely because unrelated
clauses occur in a compound parent when the child directly implements an independently meaningful
technical clause. For example, setting a component operating mode based on security posture can
not by itself establish a restrictive common secure configuration, and neither can merely
installing preconfigured signatures. Preserve exact action-and-object matches:
securing services with network controls can specialize network protection; monitoring to validate
protection effectiveness can implement effectiveness monitoring; controlling which networks may
access a service can implement logical-access authorization; and protecting data at rest or in
transit with encryption can implement the matching confidentiality clause without establishing
every confidentiality, integrity, and availability outcome only when confidentiality is an
independently stated parent clause. Reject a single combined confidentiality-integrity-availability
outcome unless the child explicitly establishes all three. Authorized-access mechanisms can
likewise implement logical-access authorization. Conversely, restricting access to a service does
not prohibit or restrict use of the service, function, protocol, or software itself. Disabling
public network access does implement prohibition of a public-network connection, and monitoring
specifically to validate protection effectiveness does implement effectiveness monitoring.
Likewise, monitoring and alerts used to validate protection effectiveness can implement the
independently meaningful clause that assesses whether controls operate as intended and produce the
desired outcome; surrounding assessment planning and reporting duties do not negate that partial
refinement. Standardized mechanism terms carry bounded semantics: IDS/IPS states intrusion
detection or prevention; SIEM states centralized security-event analysis; a VPN explicitly
described as secure states protected transmission; flow logs state network-traffic logging and
visibility; private endpoints or access points state private separation; static analysis and
vulnerability management state their named checks; and EDR or anti-malware states endpoint or
malicious-code detection and protection. Static application security testing before deployment can
implement static analysis and secure-development integration; an edge firewall can implement
between-domain or external-traffic enforcement; secure VPN or IPsec connectivity can implement
protected transmission; and tuning detection signatures for fidelity can implement attack
detection and monitoring-result accuracy. Blocking access specifically as compensation for an
inability to disable insecure services or protocols can restrict their effective use. Keep links
based on those exact semantics, but reject any
additional actor, location, governance, lifecycle, or outcome not stated by the child. Return one
explicit judgment for every supplied candidate and grounded excerpts matching the schema.
"""


class _CandidateAssessment(Model):
    parent_concept_id: UUID
    verdict: Literal["refines", "not_refines"]
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=4000)
    child_excerpt: str = Field(min_length=1)
    parent_excerpt: str = Field(min_length=1)


class _BatchAssessment(Model):
    assessments: tuple[_CandidateAssessment, ...] = Field(
        min_length=1, max_length=ASSESSMENT_BATCH_SIZE
    )


@dataclass(frozen=True)
class _AssessmentResult:
    assessment: _BatchAssessment | None
    status: Literal["completed", "failed", "context_limit"]
    method: str
    usage: Usage


@dataclass(frozen=True)
class _SelectionResult:
    selection: TraceabilitySelection | None
    status: Literal["completed", "failed", "context_limit"]
    method: str
    usage: Usage


def _input(assertion: TraceabilityAssertion) -> dict[str, object]:
    return {
        "concept_id": str(assertion.concept_id),
        "statement": assertion.statement,
        "excerpt": assertion.excerpt,
        "semantic_type": assertion.semantic_type,
        "scope": assertion.scope.model_dump(mode="json"),
        "source_key": assertion.source_key,
        "framework_references": [
            reference.model_dump(mode="json") for reference in assertion.framework_references
        ],
    }


def _explicit_mechanism_supports(child_statement: str, parent_statement: str) -> bool:
    child = child_statement.casefold()
    parent = parent_statement.casefold()
    private_access = any(
        mechanism in child
        for mechanism in ("private link", "private endpoint", "private access point")
    )
    if (
        private_access
        and "private" in child
        and ("separate information flows" in parent or "network integrity is protected" in parent)
    ):
        return True
    host_detection = "host" in child and any(
        mechanism in child for mechanism in ("ids", "ips", "intrusion detection", "edr")
    )
    return host_detection and (
        "host-based monitoring" in parent or "monitor the system to detect" in parent
    )


class OpenAITraceabilityMapper:
    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        self.chat = OpenAIInference(
            settings.model_copy(update={"model_id": settings.reconciliation_model_id}),
            client=client,
        )
        self.fingerprint = fingerprint(
            implementation="control-traceability-v7",
            model=settings.reconciliation_model_id,
            endpoint=settings.model_endpoint,
            instructions=INSTRUCTIONS,
            arbitration_instructions=ARBITRATION_INSTRUCTIONS,
            critic_instructions=CRITIC_INSTRUCTIONS,
            assessment_batch_size=ASSESSMENT_BATCH_SIZE,
            schema=_BatchAssessment.model_json_schema(),
            arbitration_schema=TraceabilitySelection.model_json_schema(),
            structured=settings.model_structured_output,
            context=settings.model_context_tokens,
            output=settings.model_max_output_tokens,
            temperature=settings.model_temperature,
            token_parameter=settings.model_max_token_parameter,
        )

    async def map(
        self,
        child: TraceabilityAssertion,
        parents: tuple[TraceabilityAssertion, ...],
    ) -> TraceabilityVerification:
        if not parents:
            return TraceabilityVerification(
                selection=TraceabilitySelection(unresolved_reason="No candidate parents supplied."),
                method="small_model_batched",
                model_id=self.chat.capabilities.model_id,
            )
        results: list[_AssessmentResult] = []
        assessments: list[_CandidateAssessment] = []
        input_order = {parent.concept_id: index for index, parent in enumerate(parents)}
        for start in range(0, len(parents), ASSESSMENT_BATCH_SIZE):
            result = await self._assess_batch(
                child,
                parents[start : start + ASSESSMENT_BATCH_SIZE],
            )
            results.append(result)
            if result.status != "completed" or result.assessment is None:
                usage = total_usage([item.usage for item in results])
                return TraceabilityVerification(
                    selection=TraceabilitySelection(
                        unresolved_reason="Candidate assessment did not complete."
                    ),
                    method=result.method,
                    status=result.status,
                    model_id=self.chat.capabilities.model_id,
                    model_calls=usage.model_calls,
                    usage=usage,
                )
            assessments.extend(result.assessment.assessments)
        accepted = sorted(
            (item for item in assessments if item.verdict == "refines"),
            key=lambda item: (-item.confidence, input_order[item.parent_concept_id]),
        )
        if accepted:
            arbitration = await self._arbitrate(
                child,
                tuple(
                    parent
                    for parent in parents
                    if parent.concept_id in {item.parent_concept_id for item in accepted}
                ),
                accepted,
            )
            results.append(
                _AssessmentResult(
                    assessment=None,
                    status=arbitration.status,
                    method=arbitration.method,
                    usage=arbitration.usage,
                )
            )
            if arbitration.status != "completed" or arbitration.selection is None:
                usage = total_usage([item.usage for item in results])
                return TraceabilityVerification(
                    selection=TraceabilitySelection(
                        unresolved_reason="Final candidate arbitration did not complete."
                    ),
                    method=arbitration.method,
                    status=arbitration.status,
                    model_id=self.chat.capabilities.model_id,
                    model_calls=usage.model_calls,
                    usage=usage,
                )
            selection = arbitration.selection
            if selection.links:
                selected_by_id = {link.parent_concept_id: link for link in selection.links}
                critique = await self._assess_batch(
                    child,
                    tuple(parent for parent in parents if parent.concept_id in selected_by_id),
                    instructions=INSTRUCTIONS + "\n" + CRITIC_INSTRUCTIONS,
                    phase="adversarial_critique",
                    span_name="traceability.control_critique",
                )
                results.append(critique)
                if critique.status != "completed" or critique.assessment is None:
                    usage = total_usage([item.usage for item in results])
                    return TraceabilityVerification(
                        selection=TraceabilitySelection(
                            unresolved_reason="Adversarial link critique did not complete."
                        ),
                        method=critique.method,
                        status=critique.status,
                        model_id=self.chat.capabilities.model_id,
                        model_calls=usage.model_calls,
                        usage=usage,
                    )
                parent_by_id = {parent.concept_id: parent for parent in parents}
                kept: list[ParentSelection] = []
                for item in critique.assessment.assessments:
                    if item.verdict == "refines":
                        kept.append(
                            ParentSelection(
                                parent_concept_id=item.parent_concept_id,
                                confidence=item.confidence,
                                rationale=item.rationale,
                                child_excerpt=item.child_excerpt,
                                parent_excerpt=item.parent_excerpt,
                            )
                        )
                    elif _explicit_mechanism_supports(
                        child.statement,
                        parent_by_id[item.parent_concept_id].statement,
                    ):
                        kept.append(selected_by_id[item.parent_concept_id])
                selection = (
                    TraceabilitySelection(links=tuple(kept))
                    if kept
                    else TraceabilitySelection(
                        unresolved_reason="Selected links failed adversarial critique."
                    )
                )
        else:
            selection = TraceabilitySelection(
                unresolved_reason="No supplied parent passed explicit candidate assessment."
            )
        usage = total_usage([item.usage for item in results])
        return TraceabilityVerification(
            selection=selection,
            method="small_model_batched",
            model_id=self.chat.capabilities.model_id,
            model_calls=usage.model_calls,
            usage=usage,
        )

    async def _assess_batch(
        self,
        child: TraceabilityAssertion,
        parents: tuple[TraceabilityAssertion, ...],
        *,
        instructions: str = INSTRUCTIONS,
        phase: str = "candidate_assessment",
        span_name: str = "traceability.control_assessment",
    ) -> _AssessmentResult:
        settings = self.chat.settings
        messages = [
            {
                "role": "system",
                "content": instructions + "\n" + json.dumps(_BatchAssessment.model_json_schema()),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "phase": phase,
                        "child": _input(child),
                        "candidate_parents": [_input(parent) for parent in parents],
                    }
                ),
            },
        ]
        parent_by_id = {parent.concept_id: parent for parent in parents}
        started = time.monotonic()
        calls = tokens_in = tokens_out = failures = metered_responses = 0
        assessment: _BatchAssessment | None = None
        method = "model_failed"
        status: Literal["completed", "failed", "context_limit"] = "failed"
        for _ in range(settings.model_retries + 1):
            if (
                sum(len(message["content"].encode()) for message in messages)
                + settings.model_max_output_tokens
                > settings.model_context_tokens
            ):
                method = "context_limit"
                status = "context_limit"
                break

            async def complete() -> tuple[str, int, int]:
                nonlocal calls
                calls += 1
                return await self.chat._complete(messages, _BatchAssessment)

            try:
                with tracer.start_as_current_span(span_name):
                    text, used_in, used_out = await retry(complete)
                tokens_in += used_in
                tokens_out += used_out
                if used_in > 0 and used_out > 0:
                    metered_responses += 1
                candidate = _BatchAssessment.model_validate_json(text)
                candidate_ids = [item.parent_concept_id for item in candidate.assessments]
                if len(candidate_ids) != len(set(candidate_ids)) or set(candidate_ids) != set(
                    parent_by_id
                ):
                    raise ValueError("Traceability output must assess every supplied parent once")
                for item in candidate.assessments:
                    parent = parent_by_id[item.parent_concept_id]
                    if item.child_excerpt not in child.excerpt:
                        raise ValueError("Child evidence is not grounded in the supplied excerpt")
                    if item.parent_excerpt not in parent.excerpt:
                        raise ValueError("Parent evidence is not grounded in the supplied excerpt")
                assessment, method, status = candidate, "small_model", "completed"
                break
            except (ValidationError, ValueError):
                failures += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Invalid IDs, coverage, schema, or evidence. Assess every supplied "
                            "parent exactly once and return grounded JSON only."
                        ),
                    }
                )
            except httpx.HTTPError:
                break
        usage = Usage(
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            model_calls=calls,
            validation_failures=failures,
            latency_ms=(time.monotonic() - started) * 1000,
            unmetered_model_calls=calls - metered_responses,
            cost=(
                (tokens_in * settings.model_input_price + tokens_out * settings.model_output_price)
                / 1_000_000
                if settings.model_input_price is not None
                and settings.model_output_price is not None
                and calls > 0
                and metered_responses == calls
                else None
            ),
        )
        return _AssessmentResult(
            assessment=assessment,
            method=method,
            status=status,
            usage=usage,
        )

    async def _arbitrate(
        self,
        child: TraceabilityAssertion,
        parents: tuple[TraceabilityAssertion, ...],
        assessments: list[_CandidateAssessment],
    ) -> _SelectionResult:
        settings = self.chat.settings
        parent_by_id = {parent.concept_id: parent for parent in parents}
        messages = [
            {
                "role": "system",
                "content": (
                    INSTRUCTIONS
                    + "\n"
                    + ARBITRATION_INSTRUCTIONS
                    + "\n"
                    + json.dumps(TraceabilitySelection.model_json_schema())
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "child": _input(child),
                        "candidate_parents": [_input(parent) for parent in parents],
                        "candidate_assessments": [
                            item.model_dump(mode="json") for item in assessments
                        ],
                    }
                ),
            },
        ]
        started = time.monotonic()
        calls = tokens_in = tokens_out = failures = metered_responses = 0
        selection: TraceabilitySelection | None = None
        method = "model_failed"
        status: Literal["completed", "failed", "context_limit"] = "failed"
        for _ in range(settings.model_retries + 1):
            if (
                sum(len(message["content"].encode()) for message in messages)
                + settings.model_max_output_tokens
                > settings.model_context_tokens
            ):
                method = "context_limit"
                status = "context_limit"
                break

            async def complete() -> tuple[str, int, int]:
                nonlocal calls
                calls += 1
                return await self.chat._complete(messages, TraceabilitySelection)

            try:
                with tracer.start_as_current_span("traceability.control_arbitration"):
                    text, used_in, used_out = await retry(complete)
                tokens_in += used_in
                tokens_out += used_out
                if used_in > 0 and used_out > 0:
                    metered_responses += 1
                candidate = TraceabilitySelection.model_validate_json(text)
                for link in candidate.links:
                    parent = parent_by_id.get(link.parent_concept_id)
                    if parent is None:
                        raise ValueError("Traceability output referenced an unknown parent")
                    if link.child_excerpt not in child.excerpt:
                        raise ValueError("Child evidence is not grounded in the supplied excerpt")
                    if link.parent_excerpt not in parent.excerpt:
                        raise ValueError("Parent evidence is not grounded in the supplied excerpt")
                selection, method, status = candidate, "small_model", "completed"
                break
            except (ValidationError, ValueError):
                failures += 1
                messages.append(
                    {
                        "role": "user",
                        "content": "Invalid IDs, schema, or evidence. Return grounded JSON only.",
                    }
                )
            except httpx.HTTPError:
                break
        usage = Usage(
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            model_calls=calls,
            validation_failures=failures,
            latency_ms=(time.monotonic() - started) * 1000,
            unmetered_model_calls=calls - metered_responses,
            cost=(
                (tokens_in * settings.model_input_price + tokens_out * settings.model_output_price)
                / 1_000_000
                if settings.model_input_price is not None
                and settings.model_output_price is not None
                and calls > 0
                and metered_responses == calls
                else None
            ),
        )
        return _SelectionResult(
            selection=selection,
            method=method,
            status=status,
            usage=usage,
        )

    async def close(self) -> None:
        await self.chat.close()
