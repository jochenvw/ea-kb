"""Offline solution-assessment evaluation over the production planner, analyzer and reducer.

Expected dispositions are evaluator metadata only; the task receives the inputs a caller would
supply (baseline, design, template and parameters) and never the expected outcome.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_evals import Case as EvalCase
from pydantic_evals import Dataset as EvalDataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from ekb.adapters.implementation.arm import ANALYZER_VERSION, ArmTemplateAnalyzer
from ekb.domain.architectures import ArchitectureControl, SolutionArchitectureRevision
from ekb.domain.assessment import (
    AssessmentDisposition,
    AssessmentReference,
    AssessmentTarget,
    DesignStatus,
    VerificationStatus,
    assess_solution,
)
from ekb.domain.baselines import (
    BaselineApproval,
    BaselinePolicy,
    BaselineRequirement,
    DesignDecision,
    DesignDocument,
    ExceptionApproval,
    SolutionIdentity,
    VerificationOption,
    WorkloadContext,
    basis_fingerprint,
    basis_gaps,
    plan_requirements,
    with_observations,
)
from ekb.domain.models import Model, Scope, content_hash, stable_id

PASSING = {"conforms", "excepted", "not_applicable"}
UNRESOLVED = {"unknown", "decision_required"}
type Versions = Mapping[str, tuple[UUID, UUID]]


class RequirementSpec(Model):
    """A synthetic requirement addressed by a readable key instead of persisted identifiers."""

    key: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=1000)
    family: str | None = Field(default=None, max_length=100)
    scope: Scope = Field(default_factory=Scope)
    when: WorkloadContext = Field(default_factory=WorkloadContext)
    excluded_reason: str | None = None
    options: tuple[VerificationOption, ...] = ()


class DecisionSpec(Model):
    requirement: str = Field(min_length=1, max_length=100)
    # "stale" binds the decision to a superseded requirement version.
    version: Literal["current", "stale"] = "current"
    kind: Literal["implements", "disabled", "exception"] = "implements"
    option: str | None = None
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_location: str = Field(min_length=1, max_length=2000)
    resources: tuple[str, ...] = ()
    exception: ExceptionApproval | None = None


class DesignSpec(Model):
    solution: SolutionIdentity = Field(default_factory=SolutionIdentity)
    context: WorkloadContext
    decisions: tuple[DecisionSpec, ...] = ()


def synthetic_versions(keys: list[str] | tuple[str, ...]) -> dict[str, tuple[UUID, UUID]]:
    return {
        key: (stable_id("eval-requirement", key), stable_id("eval-requirement-version", key, "1"))
        for key in keys
    }


def stale_version(version_id: UUID) -> UUID:
    return stable_id("superseded-requirement-version", version_id)


def build_policy(
    policy_id: str,
    version: str,
    statement: str,
    specs: list[RequirementSpec] | tuple[RequirementSpec, ...],
    versions: Versions,
) -> BaselinePolicy:
    return BaselinePolicy(
        id=policy_id,
        version=version,
        statement=statement,
        requirements=tuple(
            BaselineRequirement(
                concept_id=versions[spec.key][0],
                version_id=versions[spec.key][1],
                scope=spec.scope,
                when=spec.when,
                excluded_reason=spec.excluded_reason,
                options=spec.options,
            )
            for spec in specs
        ),
    )


def build_design(spec: DesignSpec, versions: Versions) -> DesignDocument:
    return DesignDocument(
        solution=spec.solution,
        context=spec.context,
        decisions=tuple(
            DesignDecision(
                concept_id=versions[item.requirement][0],
                version_id=versions[item.requirement][1]
                if item.version == "current"
                else stale_version(versions[item.requirement][1]),
                kind=item.kind,
                option=item.option,
                rationale=item.rationale,
                evidence_location=item.evidence_location,
                resources=item.resources,
                exception=item.exception,
            )
            for item in spec.decisions
        ),
    )


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class ExpectedFinding(Model):
    disposition: AssessmentDisposition
    applicability: Literal["applicable", "not_applicable", "unknown"] | None = None
    implementation: VerificationStatus | None = None


class AssessmentEvaluationCase(Model):
    id: str
    family: str
    scenario: str
    baseline: tuple[str, ...] = Field(min_length=1)
    policy_version: str = "1"
    context: dict[str, str | None] = Field(default_factory=dict[str, str | None])
    decisions: tuple[DecisionSpec, ...] = ()
    template: str
    parameters: dict[str, object] = Field(default_factory=dict[str, object])
    missing_authority: tuple[str, ...] = ()
    invalidates: str | None = None
    expected: dict[str, ExpectedFinding]
    expected_conforms: bool
    expected_acceptable: bool

    @model_validator(mode="after")
    def complete_expectations(self) -> Self:
        if set(self.expected) != set(self.baseline):
            raise ValueError(f"{self.id}: expectations must cover exactly the baseline")
        return self


class AssessmentEvaluationDataset(Model):
    version: str
    review_status: Literal["provisional", "independently_reviewed"]
    as_of: date
    obligation: str
    solution: SolutionIdentity
    context: WorkloadContext
    requirements: tuple[RequirementSpec, ...] = Field(min_length=4)
    templates: dict[str, dict[str, object]]
    cases: tuple[AssessmentEvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def references_resolve(self) -> Self:
        keys = {item.key for item in self.requirements}
        ids = {case.id for case in self.cases}
        for case in self.cases:
            if not set(case.baseline) <= keys or case.template not in self.templates:
                raise ValueError(f"{case.id}: unknown requirement or template")
            if case.invalidates and case.invalidates not in ids:
                raise ValueError(f"{case.id}: unknown invalidated case")
        return self


class FindingOutput(Model):
    disposition: AssessmentDisposition
    applicability: Literal["applicable", "not_applicable", "unknown"]
    design: DesignStatus
    implementation: VerificationStatus
    provenance_exact: bool
    explanation: str


class AssessmentEvaluationOutput(Model):
    findings: dict[str, FindingOutput]
    conforms: bool
    acceptable: bool
    fingerprint: str
    invalidated: bool | None = None


def run_case(
    dataset: AssessmentEvaluationDataset, case: AssessmentEvaluationCase
) -> AssessmentEvaluationOutput:
    specs = {item.key: item for item in dataset.requirements}
    versions = synthetic_versions(list(specs))
    policy = build_policy(
        "evaluation-baseline",
        case.policy_version,
        dataset.obligation,
        [specs[key] for key in case.baseline],
        versions,
    )
    policy_sha256 = content_hash(policy.model_dump_json().encode())
    approval = BaselineApproval(
        concept_id=stable_id("eval-baseline"),
        version_id=stable_id("eval-baseline", case.policy_version),
    )
    root = (
        None
        if "root" in case.missing_authority
        else AssessmentReference(
            concept_id=approval.concept_id,
            version_id=approval.version_id,
            statement=dataset.obligation,
            evidence_location=f"eval:baseline@{case.policy_version}",
        )
    )
    references = {
        versions[key][0]: None
        if key in case.missing_authority
        else AssessmentReference(
            concept_id=versions[key][0],
            version_id=versions[key][1],
            statement=specs[key].statement,
            evidence_location=f"eval:requirements#{key}",
        )
        for key in case.baseline
    }
    scope = Scope(application="Evaluation solution", environment="production")
    architecture_id = stable_id("eval-solution", case.id)
    architecture = SolutionArchitectureRevision(
        id=stable_id(architecture_id, "1"),
        architecture_id=architecture_id,
        name="Evaluation solution",
        description="Synthetic evaluation solution.",
        owner=dataset.solution.owner or "Synthetic owner",
        canonical_source=f"urn:eval:{case.id}",
        version="1",
        scope=scope,
        artifact_key="0" * 64,
        artifact_name="design.json",
        media_type="application/json",
        content_hash="0" * 64,
        # The author's checklist lists only the first control; planning must not depend on it.
        controls=(
            ArchitectureControl(
                concept_id=versions[case.baseline[0]][0],
                concept_version_id=versions[case.baseline[0]][1],
            ),
        ),
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    context = WorkloadContext.model_validate({**dataset.context.model_dump(), **case.context})
    design = build_design(
        DesignSpec(solution=dataset.solution, context=context, decisions=case.decisions),
        versions,
    )
    template = dataset.templates[case.template]
    target = AssessmentTarget(
        solution_id=architecture_id,
        architecture_revision_id=architecture.id,
        artifact_sha256=content_hash(canonical_json(template)),
        parameters_sha256=content_hash(canonical_json(case.parameters)),
        scope=scope,
        as_of=dataset.as_of,
        owner=dataset.solution.owner,
        team=dataset.solution.team,
        repository=dataset.solution.repository,
    )
    requirements = plan_requirements(policy, references, root, architecture, design, target)
    observations = ArmTemplateAnalyzer().observe(
        template,
        case.parameters,
        [item.check for item in requirements if item.check],
        artifact_sha256=target.artifact_sha256,
        basis=target.basis,
    )
    result = assess_solution(
        target,
        with_observations(requirements, observations),
        baseline_revision=policy_sha256,
        baseline_complete=root is not None and all(item.authoritative for item in requirements),
        baseline_completeness=policy.completeness,
        basis_fingerprint=basis_fingerprint(policy_sha256, approval, design, target, references),
        gaps=basis_gaps(design, target),
    )
    keys = {versions[key][0]: key for key in case.baseline}
    findings: dict[str, FindingOutput] = {}
    for finding in result.findings:
        key = keys[finding.requirement.concept_id]
        current = references[finding.requirement.concept_id]
        exact = (
            finding.provenance == (root, current)
            if finding.authoritative
            else root not in finding.provenance
        )
        findings[key] = FindingOutput(
            disposition=finding.disposition,
            applicability=finding.applicability,
            design=finding.design,
            implementation=finding.implementation,
            provenance_exact=exact
            and finding.requirement.version_id == versions[key][1]
            and all(item.evidence_location for item in finding.provenance),
            explanation=finding.explanation,
        )
    # Unchanged policy, design and implementation inputs must reproduce the same basis.
    return AssessmentEvaluationOutput(
        findings=findings,
        conforms=result.conforms,
        acceptable=result.acceptable_with_exceptions,
        fingerprint=result.basis_fingerprint or "",
    )


@dataclass
class AssessmentQuality(Evaluator[str, AssessmentEvaluationOutput, AssessmentEvaluationCase]):
    def get_evaluator_version(self) -> str:
        return "solution-assessment-v2"

    def evaluate(
        self,
        ctx: EvaluatorContext[str, AssessmentEvaluationOutput, AssessmentEvaluationCase],
    ) -> dict[str, bool | int]:
        return case_metrics(ctx.metadata, ctx.output) if ctx.metadata else {"exact": False}


def case_metrics(
    case: AssessmentEvaluationCase, output: AssessmentEvaluationOutput
) -> dict[str, bool | int]:
    expected, actual = case.expected, output.findings
    exceptions = {item.requirement for item in case.decisions if item.kind == "exception"}
    omissions = {key for key, item in expected.items() if item.disposition == "decision_required"}
    applicability = {key: item for key, item in expected.items() if item.applicability}
    mismatched = [
        key
        for key, item in expected.items()
        if actual[key].disposition != item.disposition
        or (item.implementation and actual[key].implementation != item.implementation)
        or (item.applicability and actual[key].applicability != item.applicability)
    ]
    false_passes = sum(
        actual[key].disposition in PASSING and item.disposition not in PASSING
        for key, item in expected.items()
    ) + int(output.conforms and not case.expected_conforms)
    return {
        "exact": not mismatched
        and output.conforms is case.expected_conforms
        and output.acceptable is case.expected_acceptable
        and output.invalidated is not False
        and all(
            item.provenance_exact
            for key, item in actual.items()
            if key not in case.missing_authority and "root" not in case.missing_authority
        ),
        "false_passes": false_passes,
        "false_violations": sum(
            actual[key].disposition == "violated" and item.disposition != "violated"
            for key, item in expected.items()
        ),
        "omissions_expected": len(omissions),
        "omissions_detected": sum(
            actual[key].disposition == "decision_required" for key in omissions
        ),
        "applicability_expected": len(applicability),
        "applicability_correct": sum(
            actual[key].applicability == item.applicability for key, item in applicability.items()
        ),
        "exceptions_expected": len(exceptions),
        "exceptions_correct": sum(
            actual[key].disposition == expected[key].disposition for key in exceptions
        ),
        "unresolved_expected": sum(item.disposition in UNRESOLVED for item in expected.values()),
        "unresolved_reported": sum(
            actual[key].disposition == item.disposition
            for key, item in expected.items()
            if item.disposition in UNRESOLVED
        ),
        "provenance_exact": sum(item.provenance_exact for item in actual.values()),
        "findings": len(actual),
    }


METRICS = (
    "false_passes",
    "false_violations",
    "omissions_expected",
    "omissions_detected",
    "applicability_expected",
    "applicability_correct",
    "exceptions_expected",
    "exceptions_correct",
    "unresolved_expected",
    "unresolved_reported",
    "provenance_exact",
    "findings",
)


async def evaluate_architectures(
    dataset_path: Path = Path("evals") / "architecture-assessment-v2.json",
    output: Path = Path("data") / "architecture-assessment.json",
) -> dict[str, Any]:
    raw = await asyncio.to_thread(dataset_path.read_bytes)
    dataset = AssessmentEvaluationDataset.model_validate_json(raw)
    by_id = {case.id: case for case in dataset.cases}

    async def task(case_id: str) -> AssessmentEvaluationOutput:
        case = by_id[case_id]
        result = await asyncio.to_thread(run_case, dataset, case)
        if case.invalidates:
            prior = await asyncio.to_thread(run_case, dataset, by_id[case.invalidates])
            result = result.model_copy(
                update={"invalidated": result.fingerprint != prior.fingerprint}
            )
        return result

    framework = await EvalDataset[str, AssessmentEvaluationOutput, AssessmentEvaluationCase](
        name=f"solution-assessment-{dataset.version}",
        cases=[
            EvalCase(name=case.id, inputs=case.id, metadata=case, evaluators=[AssessmentQuality()])
            for case in dataset.cases
        ],
    ).evaluate(
        task,
        task_name="ekb-solution-assessment",
        max_concurrency=4,
        progress=False,
        retry_task=None,
        retry_evaluators=None,
        metadata={
            "dataset_version": dataset.version,
            "dataset_sha256": content_hash(raw),
            "analyzer": ANALYZER_VERSION,
        },
    )
    failures = {failure.name for failure in framework.failures}
    totals = dict.fromkeys(METRICS, 0)
    cases = []
    for report in framework.cases:
        failures.update(f"{report.name}:{failure.name}" for failure in report.evaluator_failures)
        exact = report.assertions.get("exact")
        if exact is None or exact.value is not True:
            failures.add(report.name)
        for metric in METRICS:
            score = report.scores.get(metric)
            totals[metric] += int(score.value) if score else 0
        case = by_id[report.name]
        cases.append(
            {
                "id": report.name,
                "family": case.family,
                "scenario": case.scenario,
                "exact": bool(exact and exact.value is True),
            }
        )
    cases.sort(key=lambda item: item["id"])
    summary = {
        "version": dataset.version,
        "review_status": dataset.review_status,
        "dataset_sha256": content_hash(raw),
        "analyzer": ANALYZER_VERSION,
        "verification_mode": "offline_planned_template",
        "families": sorted({case.family for case in dataset.cases}),
        "metrics": totals,
        "cases": cases,
        "limitations": [
            "Synthetic baseline authority; offline template checks do not verify deployed state.",
            "Coverage is limited to the analyzer's explicit checks and supported resource types.",
        ],
        "passed": not failures
        and len(cases) == len(dataset.cases)
        and totals["false_passes"] == 0
        and totals["false_violations"] == 0,
        "failures": sorted(failures),
    }
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text, json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary
