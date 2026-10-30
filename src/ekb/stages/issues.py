from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from itertools import combinations
from typing import Literal

from ekb.domain.execution import ProcessorSpec
from ekb.domain.issues import Issue
from ekb.domain.models import AuthorityEvent, fingerprint, now, stable_id
from ekb.domain.publication import project_authority
from ekb.domain.resolution import (
    Deviation,
    Finding,
    active_aliases,
    decision_heads,
    overlaps,
    resolve_alias,
)
from ekb.observability import tracer
from ekb.ports import UnitOfWorkFactory
from ekb.ports.workflow import CheckContext, CheckResult, KnowledgeCheck
from ekb.stages import CheckedStage
from ekb.stages.enrichment import Check, pairs


def issue(
    context: CheckContext,
    identifier: str,
    kind: str,
    concepts: tuple,
    explanation: str,
    *,
    findings: tuple[Finding, ...] = (),
    occurrences: tuple = (),
    severity: Literal["critical", "high", "medium", "low"] = "medium",
    confidence: float = 0.8,
    scope=None,
) -> Issue:
    from ekb.domain.models import Scope

    scope = scope or Scope()
    identities = tuple(sorted(set(concepts), key=str))
    refs = tuple(
        sorted(set(occurrences or tuple(o for f in findings for o in f.occurrence_ids)), key=str)
    )
    return Issue(
        id=stable_id("issue", identifier, kind, identities, fingerprint(scope=scope.model_dump())),
        issue_type=kind,
        severity=severity,
        confidence=confidence,
        concept_ids=identities,
        scope=scope,
        occurrence_ids=refs,
        finding_ids=tuple(f.id for f in findings),
        check_id=identifier,
        check_version=context.check_version,
        evidence_revision=fingerprint(
            concepts=identities,
            occurrences=refs,
            condition=explanation,
        ),
        explanation=explanation,
        options=(
            "Review source evidence",
            "Keep concepts distinct",
            "Accept a governed reconciliation",
            "Dismiss finding",
        ),
    )


def unresolved(context: CheckContext) -> list[Finding]:
    heads = decision_heads(context.snapshot.decisions)
    return [
        f
        for f in context.snapshot.findings
        if f.id in context.snapshot.active_finding_ids
        and (f.id not in heads or heads[f.id].outcome in {"deferred", "reversed"})
    ]


async def unresolved_duplicates(context: CheckContext) -> CheckResult:
    results = [
        issue(
            context,
            "unresolved_duplicates",
            "unresolved_duplicate",
            f.concept_ids,
            "A possible duplicate has no accepted or rejected identity decision.",
            findings=(f,),
            confidence=f.confidence,
            scope=f.scope,
        )
        for f in unresolved(context)
        if f.action == "alias" and f.confidence >= context.options.similarity_threshold
    ]
    return CheckResult(issues=tuple(results), candidates=len(results))


async def conflicting_requirements(context: CheckContext) -> CheckResult:
    results = []
    candidates = await asyncio.to_thread(lambda: list(pairs(context)))
    for left, old, right, new, _score in candidates:
        if not overlaps(old.candidate.scope, new.candidate.scope):
            continue
        texts = (old.candidate.statement.casefold(), new.candidate.statement.casefold())
        polarity = [bool(re.search(r"\b(?:not|never)\b", t)) for t in texts]
        base = [re.sub(r"\s+", " ", re.sub(r"\b(?:not|never)\s*", "", t)).strip() for t in texts]
        if polarity[0] != polarity[1] and base[0] == base[1]:
            results.append(
                issue(
                    context,
                    "conflicting_requirements",
                    "conflicting_requirements",
                    (left.id, right.id),
                    "Opposing explicit obligations overlap in scope and time.",
                    occurrences=tuple(o.id for c in (left, right) for o in c.occurrences),
                    severity="high",
                    confidence=0.95,
                    scope=old.candidate.scope,
                )
            )
    for f in unresolved(context):
        if f.relationship and f.relationship.value == "tensionWith":
            results.append(
                issue(
                    context,
                    "conflicting_requirements",
                    "potential_trade_off",
                    f.concept_ids,
                    "A proposed tension requires review; this is not a proven hard contradiction.",
                    findings=(f,),
                    severity="low",
                    confidence=f.confidence,
                    scope=f.scope,
                )
            )
    return CheckResult(issues=tuple(results), candidates=len(results))


async def competing_canonical(context: CheckContext) -> CheckResult:
    results = []
    aliases = active_aliases(context.snapshot, now().date())
    groups = defaultdict(list)
    for concept in context.snapshot.concepts:
        groups[resolve_alias(concept.id, aliases)[-1]].append(concept)
    for concepts in groups.values():
        versions = {v.id: v for c in concepts for v in c.versions}
        canonical = []
        for concept in concepts:
            canonical.extend(
                s
                for s in project_authority(
                    [AuthorityEvent.model_validate(e) for e in concept.authority_events],
                    now().date(),
                    [v.id for v in concept.versions],
                )
                if s.canonical_status == "canonical" and s.governance_status == "approved"
            )
        superseded = set()
        for state in canonical:
            predecessor = versions[state.concept_version_id].predecessor_id
            visited = {state.concept_version_id}
            while predecessor and predecessor not in visited:
                visited.add(predecessor)
                superseded.add(predecessor)
                previous = versions.get(predecessor)
                predecessor = previous.predecessor_id if previous else None
        canonical = [s for s in canonical if s.concept_version_id not in superseded]
        for left, right in combinations(canonical, 2):
            if left.concept_version_id != right.concept_version_id and overlaps(
                left.scope, right.scope
            ):
                results.append(
                    issue(
                        context,
                        "competing_canonical_sources",
                        "competing_canonical_sources",
                        tuple(c.id for c in concepts),
                        "Multiple approved canonical versions overlap in applicability.",
                        occurrences=tuple(o.id for c in concepts for o in c.occurrences),
                        severity="high",
                        confidence=1.0,
                        scope=left.scope,
                    )
                )
    return CheckResult(issues=tuple(results), candidates=len(results))


async def version_ambiguity(context: CheckContext) -> CheckResult:
    results = []
    successors: dict = defaultdict(list)
    for f in unresolved(context):
        if f.action == "version" and len(f.concept_ids) == 2:
            successors[f.concept_ids[-1]].append(f)
    for cid, findings in successors.items():
        if len({f.concept_ids[0] for f in findings}) > 1:
            results.append(
                issue(
                    context,
                    "version_ambiguity",
                    "competing_successors",
                    (cid,),
                    "Multiple successor candidates require a version-lineage decision.",
                    findings=tuple(findings),
                )
            )
    for concept in context.snapshot.concepts:
        ids = {v.id for v in concept.versions}
        states = project_authority(
            [AuthorityEvent.model_validate(e) for e in concept.authority_events], now().date()
        )
        for version in concept.versions:
            invalid = (
                version.predecessor_id
                and version.predecessor_id not in ids
                or version.effective_from
                and version.effective_to
                and version.effective_to < version.effective_from
            )
            immature = bool(version.semantic_version and version.semantic_version.prerelease) or (
                version.publication.publication_status
                in {
                    "draft",
                    "preview",
                    "working_draft",
                    "consultation",
                    "alpha",
                    "beta",
                    "release_candidate",
                }
            )
            global_canonical = any(
                s.concept_version_id == version.id
                and s.canonical_status == "canonical"
                and not any(s.scope.model_dump().values())
                for s in states
            )
            if invalid or immature and global_canonical:
                results.append(
                    issue(
                        context,
                        "version_ambiguity",
                        "invalid_version_authority",
                        (concept.id,),
                        "Missing predecessor, invalid dates or globally canonical prerelease.",
                        occurrences=tuple(o.id for o in concept.occurrences),
                        severity="high",
                    )
                )
    return CheckResult(issues=tuple(results), candidates=len(results))


async def provenance_gaps(context: CheckContext) -> CheckResult:
    results = []
    for concept in context.snapshot.concepts:
        if not concept.occurrences:
            results.append(
                issue(
                    context,
                    "provenance_gaps",
                    "missing_provenance",
                    (concept.id,),
                    "An assertion has no exact source occurrence.",
                    severity="high",
                )
            )
    for f in unresolved(context):
        if not f.occurrence_ids:
            results.append(
                issue(
                    context,
                    "provenance_gaps",
                    "missing_relationship_provenance",
                    f.concept_ids,
                    "A relationship lacks a source evidence reference.",
                    findings=(f,),
                    severity="high",
                )
            )
    return CheckResult(issues=tuple(results), candidates=len(results))


def deviation_issues(context: CheckContext, deviations: tuple[Deviation, ...]) -> CheckResult:
    grouped: dict = defaultdict(list)
    today = now().date()
    for deviation in deviations:
        if (
            deviation.approved
            and deviation.effective_from <= today
            and (not deviation.effective_until or deviation.effective_until >= today)
        ):
            grouped[deviation.concept_id].append(deviation)
    results = []
    for cid, records in grouped.items():
        solutions = {r.solution for r in records}
        if len(solutions) > context.options.threshold:
            results.append(
                issue(
                    context,
                    "excessive_deviations",
                    "excessive_deviations",
                    (cid,),
                    f"{len(solutions)} distinct active approved solution deviations exceed "
                    f"threshold {context.options.threshold}; renewals are not extra solutions.",
                    occurrences=tuple(r.evidence_occurrence_id for r in records),
                )
            )
    return CheckResult(issues=tuple(results), candidates=len(deviations))


def issues_stage(database: UnitOfWorkFactory) -> CheckedStage:
    async def deviations(context: CheckContext) -> CheckResult:
        async with database() as uow:
            records = await uow.resolution.deviations(now().date())
        return deviation_issues(context, records)

    def observed(evaluate):
        async def execute(context: CheckContext) -> CheckResult:
            with tracer.start_as_current_span("issues.detect"):
                return await evaluate(context)

        return execute

    checks: tuple[KnowledgeCheck, ...] = tuple(
        Check(
            identifier,
            observed(evaluate),
            "2" if identifier == "competing_canonical_sources" else "1",
        )
        for identifier, evaluate in (
            ("competing_canonical_sources", competing_canonical),
            ("unresolved_duplicates", unresolved_duplicates),
            ("conflicting_requirements", conflicting_requirements),
            ("version_ambiguity", version_ambiguity),
            ("provenance_gaps", provenance_gaps),
            ("excessive_deviations", deviations),
        )
    )
    return CheckedStage(
        database,
        ProcessorSpec(
            id="issues",
            name="Issue detection",
            version="1",
            dependencies=("enrichment",),
            input_contract="enriched/v1",
            output_contract="issues/v1",
        ),
        checks,
    )
