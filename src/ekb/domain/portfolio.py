"""Read-only portfolio projections over planned solution assessments.

Exception risk (#32) and baseline change impact (#33) are review signals for humans. Neither
computes a risk score, approves aggregate risk, renews exceptions or changes a baseline.
"""

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field

from ekb.domain.assessment import (
    Applicability,
    AssessmentException,
    AssessmentReference,
    AssessmentTarget,
    RequirementAssessment,
)
from ekb.domain.baselines import BaselinePolicy, BaselineRequirement, WorkloadContext
from ekb.domain.models import Model, stable_id
from ekb.domain.resolution import applicable

ExceptionStatus = Literal[
    "active", "expiring", "expired", "future", "out_of_scope", "wrong_version", "revoked"
]
COUNTED: frozenset[str] = frozenset({"active", "expiring"})
HIGH_IMPACT = {"criticality": {"high"}, "exposure": {"public"}}


class SolutionPlan(Model):
    """One solution revision planned against one selected baseline, without implementation."""

    solution_id: UUID
    revision_id: UUID
    version: str
    name: str
    design_available: bool
    context: WorkloadContext
    target: AssessmentTarget
    requirements: tuple[RequirementAssessment, ...]
    gaps: tuple[str, ...] = ()

    def requirement(self, concept_id: UUID) -> RequirementAssessment | None:
        return next(
            (item for item in self.requirements if item.requirement.concept_id == concept_id),
            None,
        )


class ExceptionRiskInput(Model):
    baseline_concept_id: UUID
    baseline_version_id: UUID
    as_of: date | None = None
    previous_as_of: date | None = None
    window_days: int = Field(default=30, ge=0, le=365)
    review_rate: float = Field(default=0.25, gt=0, le=1)


class ExceptionRecord(Model):
    decision_id: UUID
    renews: UUID | None = None
    status: ExceptionStatus
    solution_id: UUID
    revision_id: UUID
    solution: str
    owner: str | None = None
    team: str | None = None
    repository: str | None = None
    criticality: str | None = None
    exposure: str | None = None
    requirement: AssessmentReference
    pinned_version_id: UUID
    rationale: str
    approved_by: str
    effective_from: date
    effective_until: date | None = None
    days_in_effect: int
    evidence_location: str | None = None


class RequirementExposure(Model):
    requirement: AssessmentReference
    authoritative_solutions: int
    applicable: int
    applicability_unknown: int
    not_applicable: int
    active_exceptions: int
    rate: float | None = None
    violated: int
    decision_required: int
    permitted_alternatives: int
    implementation_unverified: int
    inactive_exceptions: dict[str, int] = Field(default_factory=dict[str, int])


class ReviewSignal(Model):
    kind: Literal[
        "high_impact_exception",
        "recurring_reason",
        "concentration",
        "shared_owner",
        "expiring",
    ]
    reason: str
    requirements: tuple[UUID, ...] = ()
    solutions: tuple[UUID, ...] = ()
    decisions: tuple[UUID, ...] = ()


class RequirementTrend(Model):
    concept_id: UUID
    previous_applicable: int
    applicable: int
    previous_active: int
    active: int
    previous_rate: float | None
    rate: float | None
    population_changed: bool


class ExceptionRiskReport(Model):
    policy_id: str
    policy_version: str
    baseline_revision: str
    obligation: str
    as_of: date
    window_days: int
    review_rate: float
    population: str
    solutions: int
    solutions_without_design: int
    distinct_exception_decisions: int
    solutions_with_exceptions: int
    requirements: tuple[RequirementExposure, ...]
    by_criticality: dict[str, int]
    by_exposure: dict[str, int]
    signals: tuple[ReviewSignal, ...]
    exceptions: tuple[ExceptionRecord, ...]
    previous_as_of: date | None = None
    trend: tuple[RequirementTrend, ...] = ()
    gaps: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "Implementation evidence is not retained portfolio-wide; run a solution assessment "
        "to verify implementation.",
        "Counts are review signals, not a risk score or approval of aggregate risk.",
    )


def exception_status(
    exception: AssessmentException, target: AssessmentTarget, version_id: UUID, window_end: date
) -> ExceptionStatus:
    if exception.revoked:
        return "revoked"
    if exception.requirement_version_id != version_id:
        return "wrong_version"
    if target.as_of < exception.effective_from:
        return "future"
    if exception.effective_until is not None and target.as_of > exception.effective_until:
        return "expired"
    if not applicable(exception.scope, target.scope, target.as_of):
        return "out_of_scope"
    if exception.effective_until is not None and exception.effective_until <= window_end:
        return "expiring"
    return "active"


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _normalized(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def _record(
    plan: SolutionPlan, item: RequirementAssessment, rule: BaselineRequirement, window_end: date
) -> ExceptionRecord | None:
    exception = item.exception
    if exception is None:
        return None
    target = plan.target
    return ExceptionRecord(
        decision_id=exception.decision_id,
        renews=exception.renews,
        status=exception_status(exception, target, rule.version_id, window_end),
        solution_id=plan.solution_id,
        revision_id=plan.revision_id,
        solution=plan.name,
        owner=target.owner,
        team=target.team,
        repository=target.repository,
        criticality=plan.context.criticality,
        exposure=plan.context.exposure,
        requirement=item.requirement,
        pinned_version_id=exception.requirement_version_id,
        rationale=exception.rationale,
        approved_by=exception.approved_by,
        effective_from=exception.effective_from,
        effective_until=exception.effective_until,
        days_in_effect=max((target.as_of - exception.effective_from).days, 0),
        evidence_location=exception.evidence_location,
    )


def exception_risk(
    policy: BaselinePolicy,
    baseline_revision: str,
    plans: Sequence[SolutionPlan],
    as_of: date,
    *,
    window_days: int = 30,
    review_rate: float = 0.25,
    gaps: Sequence[str] = (),
) -> ExceptionRiskReport:
    """Project exceptions over the population of solutions the baseline applies to."""
    window_end = as_of + timedelta(days=window_days)
    exposures: list[RequirementExposure] = []
    records: list[ExceptionRecord] = []
    signals: list[ReviewSignal] = []
    counted: list[ExceptionRecord] = []
    for rule in policy.requirements:
        reference: AssessmentReference | None = None
        tallies: dict[str, int] = defaultdict(int)
        inactive: dict[str, int] = defaultdict(int)
        active: list[ExceptionRecord] = []
        for plan in plans:
            item = plan.requirement(rule.concept_id)
            if item is None:
                continue
            reference = reference or item.requirement
            if not item.authoritative:
                tallies["unknown"] += 1
                continue
            tallies["authoritative"] += 1
            tallies[item.applicability] += 1
            if item.applicability != "applicable":
                continue
            record = _record(plan, item, rule, window_end)
            if record:
                records.append(record)
            if record and record.status in COUNTED:
                active.append(record)
                continue
            if record:
                inactive[record.status] += 1
            if item.design == "violated":
                tallies["violated"] += 1
            elif item.design == "unresolved":
                tallies["decision_required"] += 1
            else:
                tallies["unverified"] += 1
                if rule.options and item.option != rule.options[0].id:
                    tallies["alternative"] += 1
        if reference is None:
            continue
        counted.extend(active)
        solutions = {item.solution_id for item in active}
        applicable_count = tallies["applicable"]
        rate = _rate(len(solutions), applicable_count)
        exposures.append(
            RequirementExposure(
                requirement=reference,
                authoritative_solutions=tallies["authoritative"],
                applicable=applicable_count,
                applicability_unknown=tallies["unknown"],
                not_applicable=tallies["not_applicable"],
                active_exceptions=len(solutions),
                rate=rate,
                violated=tallies["violated"],
                decision_required=tallies["decision_required"],
                permitted_alternatives=tallies["alternative"],
                implementation_unverified=tallies["unverified"],
                inactive_exceptions=dict(inactive),
            )
        )
        if rate is not None and rate >= review_rate and solutions:
            signals.append(
                ReviewSignal(
                    kind="concentration",
                    reason=(
                        f"{len(solutions)} of {applicable_count} applicable solutions hold an "
                        f"active exception (review threshold {review_rate:.0%})."
                    ),
                    requirements=(rule.concept_id,),
                    solutions=tuple(sorted(solutions)),
                )
            )
        reasons: dict[str, list[ExceptionRecord]] = defaultdict(list)
        for record in active:
            reasons[_normalized(record.rationale)].append(record)
        for group in reasons.values():
            if len({item.solution_id for item in group}) > 1:
                signals.append(
                    ReviewSignal(
                        kind="recurring_reason",
                        reason=(
                            f"{len(group)} solutions cite the same reason ({group[0].rationale!r});"
                            " consider guidance, a remediation pattern or an approved alternative."
                        ),
                        requirements=(rule.concept_id,),
                        solutions=tuple(sorted({item.solution_id for item in group})),
                        decisions=tuple(sorted({item.decision_id for item in group})),
                    )
                )
    for record in counted:
        impact = [
            f"{field}={value}"
            for field, values in HIGH_IMPACT.items()
            if (value := getattr(record, field)) in values
        ]
        if impact:
            signals.append(
                ReviewSignal(
                    kind="high_impact_exception",
                    reason=f"An active exception affects a {', '.join(impact)} solution.",
                    requirements=(record.requirement.concept_id,),
                    solutions=(record.solution_id,),
                    decisions=(record.decision_id,),
                )
            )
        if record.status == "expiring":
            signals.append(
                ReviewSignal(
                    kind="expiring",
                    reason=f"The exception expires on {record.effective_until}.",
                    requirements=(record.requirement.concept_id,),
                    solutions=(record.solution_id,),
                    decisions=(record.decision_id,),
                )
            )
    owners: dict[str, list[ExceptionRecord]] = defaultdict(list)
    for record in counted:
        if record.team or record.repository:
            owners[record.team or record.repository or ""].append(record)
    for owner, group in owners.items():
        if len({item.requirement.concept_id for item in group}) > 1:
            signals.append(
                ReviewSignal(
                    kind="shared_owner",
                    reason=f"{owner} holds active exceptions against several requirements.",
                    requirements=tuple(sorted({item.requirement.concept_id for item in group})),
                    solutions=tuple(sorted({item.solution_id for item in group})),
                    decisions=tuple(sorted({item.decision_id for item in group})),
                )
            )
    by_solution = {record.solution_id: record for record in counted}
    breakdown: dict[str, dict[str, int]] = {"criticality": {}, "exposure": {}}
    for field, values in breakdown.items():
        for record in by_solution.values():
            key = getattr(record, field) or "unknown"
            values[key] = values.get(key, 0) + 1
    missing = sum(not plan.design_available for plan in plans)
    report_gaps = list(gaps)
    if missing:
        report_gaps.append(f"{missing} solution(s) lack a machine-readable design document.")
    if any(item.applicability_unknown for item in exposures):
        report_gaps.append("Some solutions have unknown applicability; they are not excluded.")
    return ExceptionRiskReport(
        policy_id=policy.id,
        policy_version=policy.version,
        baseline_revision=baseline_revision,
        obligation=policy.statement,
        as_of=as_of,
        window_days=window_days,
        review_rate=review_rate,
        population=(
            "Latest registered revision on or before as_of of every solution, assessed against "
            "the selected baseline; rates use applicable solutions as denominator."
        ),
        solutions=len(plans),
        solutions_without_design=missing,
        distinct_exception_decisions=len({record.decision_id for record in counted}),
        solutions_with_exceptions=len(by_solution),
        requirements=tuple(exposures),
        by_criticality=breakdown["criticality"],
        by_exposure=breakdown["exposure"],
        signals=tuple(signals),
        exceptions=tuple(records),
        gaps=tuple(report_gaps),
    )


def compare(current: ExceptionRiskReport, previous: ExceptionRiskReport) -> ExceptionRiskReport:
    """Compare like-for-like requirement populations and disclose population changes."""
    before = {item.requirement.concept_id: item for item in previous.requirements}
    trend: list[RequirementTrend] = []
    for item in current.requirements:
        prior = before.get(item.requirement.concept_id)
        trend.append(
            RequirementTrend(
                concept_id=item.requirement.concept_id,
                previous_applicable=prior.applicable if prior else 0,
                applicable=item.applicable,
                previous_active=prior.active_exceptions if prior else 0,
                active=item.active_exceptions,
                previous_rate=prior.rate if prior else None,
                rate=item.rate,
                population_changed=prior is None or prior.applicable != item.applicable,
            )
        )
    gaps = list(current.gaps)
    if previous.baseline_revision != current.baseline_revision:
        gaps.append("The baseline differs between periods; trends are not like-for-like.")
    return current.model_copy(
        update={"previous_as_of": previous.as_of, "trend": tuple(trend), "gaps": tuple(gaps)}
    )


ChangeStage = Literal["proposed_adoption", "approved_not_yet_effective", "approved_effective"]
ChangeKind = Literal["added", "removed", "changed", "unresolved", "publisher_release"]
Certainty = Literal["confirmed", "potential", "unresolved"]


class ChangeImpactInput(Model):
    old_concept_id: UUID
    old_version_id: UUID
    new_concept_id: UUID
    new_version_id: UUID
    stage: Literal["proposed", "approved"] = "proposed"
    effective_from: date | None = None
    as_of: date | None = None


class RequirementChange(Model):
    concept_id: UUID
    kind: ChangeKind
    aspects: tuple[str, ...] = ()
    old_version_id: UUID | None = None
    new_version_id: UUID | None = None
    release_version_id: UUID | None = None
    statement: str


class ImpactItem(Model):
    id: UUID
    change: RequirementChange
    solution_id: UUID
    revision_id: UUID
    solution: str
    version: str
    owner: str | None = None
    team: str | None = None
    repository: str | None = None
    applicability_before: Applicability | None = None
    applicability_after: Applicability | None = None
    certainty: Certainty
    reason: str
    action: str
    evidence: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()


class ImpactDraft(Model):
    """Owner-directed work draft; creating external work items needs an explicit caller."""

    route: str
    title: str
    body: str
    items: tuple[UUID, ...]


class ChangeImpactReport(Model):
    stage: ChangeStage
    old_policy: str
    new_policy: str
    old_revision: str
    new_revision: str
    effective_from: date | None = None
    as_of: date
    changes: tuple[RequirementChange, ...]
    items: tuple[ImpactItem, ...]
    drafts: tuple[ImpactDraft, ...]
    gaps: tuple[str, ...] = ()


def _aspects(old: BaselineRequirement, new: BaselineRequirement) -> tuple[str, ...]:
    aspects = (
        ("version", old.version_id != new.version_id),
        ("scope", old.scope != new.scope),
        ("conditions", old.when != new.when),
        ("tailoring", old.excluded_reason != new.excluded_reason),
        ("options", old.options != new.options),
    )
    return tuple(name for name, changed in aspects if changed)


def diff_baselines(
    old: BaselinePolicy,
    new: BaselinePolicy,
    references: Mapping[UUID, AssessmentReference | None],
    releases: Mapping[UUID, UUID] | None = None,
) -> tuple[RequirementChange, ...]:
    """Match requirements by concept identity only, never by control number or similarity."""
    before = {item.concept_id: item for item in old.requirements}
    after = {item.concept_id: item for item in new.requirements}
    changes: list[RequirementChange] = []
    for concept_id, rule in after.items():
        reference = references.get(concept_id)
        statement = reference.statement if reference else "Requirement authority not established."
        prior = before.get(concept_id)
        if reference is None:
            kind: ChangeKind = "unresolved"
            aspects = _aspects(prior, rule) if prior else ()
        elif prior is None:
            kind, aspects = "added", ()
        elif aspects := _aspects(prior, rule):
            kind = "changed"
        elif releases and concept_id in releases:
            kind = "publisher_release"
        else:
            continue
        changes.append(
            RequirementChange(
                concept_id=concept_id,
                kind=kind,
                aspects=aspects,
                old_version_id=prior.version_id if prior else None,
                new_version_id=rule.version_id,
                release_version_id=(releases or {}).get(concept_id),
                statement=statement,
            )
        )
    for concept_id, rule in before.items():
        if concept_id not in after:
            changes.append(
                RequirementChange(
                    concept_id=concept_id,
                    kind="removed",
                    old_version_id=rule.version_id,
                    statement=(
                        reference.statement
                        if (reference := references.get(concept_id))
                        else "Removed requirement."
                    ),
                )
            )
    return tuple(changes)


def _assess(
    change: RequirementChange,
    before: RequirementAssessment | None,
    after: RequirementAssessment | None,
) -> tuple[Certainty, str, str] | None:
    """Return certainty, reason and action, or None when the solution is unaffected."""
    old = before.applicability if before else None
    new = after.applicability if after else None
    if change.kind == "unresolved":
        return (
            "unresolved",
            "The new requirement has no established enterprise authority.",
            "Establish approved authority before assessing this change.",
        )
    relevant = new if change.kind != "removed" else old
    if relevant == "unknown" or (change.kind == "changed" and old == "unknown"):
        known = after or before
        return (
            "unresolved",
            known.applicability_reason if known else "Applicability is unknown.",
            "Supply the missing workload context or scope to decide whether the change applies.",
        )
    if change.kind == "publisher_release":
        if new != "applicable":
            return None
        return (
            "potential",
            "The publisher released a newer requirement version that the baseline has not adopted.",
            "Review the release; adoption needs an approved baseline change.",
        )
    if change.kind == "added":
        if new != "applicable":
            return None
        return (
            "confirmed",
            "The baseline adds a requirement that applies to this solution.",
            "Record a version-bound design decision and verify the implementation.",
        )
    if change.kind == "removed":
        if old != "applicable" or not before or not (before.decision_location or before.exception):
            return None
        return (
            "potential",
            "The baseline removes a requirement this solution has decisions for.",
            "Decide whether to retire or locally retain the existing decision.",
        )
    if after is None:
        return None
    if before is None:
        # A solution first planned under the new baseline has no prior decision to carry.
        if new != "applicable":
            return None
        return (
            "confirmed",
            "The changed requirement applies to a solution with no prior assessment.",
            "Record a version-bound design decision and verify the implementation.",
        )
    if old == "applicable" and new == "not_applicable":
        return (
            "potential",
            "The changed requirement no longer applies to this solution.",
            "Confirm non-applicability and retire obsolete decisions or exceptions.",
        )
    if new != "applicable":
        return None
    if before.exception:
        return (
            "confirmed",
            "An exception approved for the previous requirement does not carry to the new one.",
            "Renew the exception for the new version or remediate.",
        )
    if "version" in change.aspects:
        return (
            "confirmed",
            "The requirement version changed; existing decisions are bound to the old version.",
            "Revalidate the design decision against the new requirement version.",
        )
    if old != "applicable":
        return (
            "confirmed",
            "The changed scope or conditions now make the requirement apply.",
            "Record a version-bound design decision and verify the implementation.",
        )
    if before.design != after.design:
        return (
            "confirmed",
            f"The design status changes from {before.design} to {after.design}.",
            "Update the design to a permitted option or obtain an approved exception.",
        )
    return None


def _routing_gaps(plan: SolutionPlan) -> tuple[str, ...]:
    target = plan.target
    labels = (("owner", "owner"), ("team", "responsible team"), ("repository", "repository"))
    return tuple(
        f"The solution {label} is not recorded."
        for field, label in labels
        if not getattr(target, field)
    )


def change_impact(
    changes: Sequence[RequirementChange],
    before: Mapping[UUID, SolutionPlan],
    after: Mapping[UUID, SolutionPlan],
    *,
    stage: ChangeStage,
    old: tuple[BaselinePolicy, str],
    new: tuple[BaselinePolicy, str],
    as_of: date,
    effective_from: date | None = None,
    gaps: Sequence[str] = (),
) -> ChangeImpactReport:
    items: list[ImpactItem] = []
    for solution_id in sorted(set(before) | set(after)):
        plan = after.get(solution_id) or before[solution_id]
        prior = before.get(solution_id)
        for change in changes:
            old_item = prior.requirement(change.concept_id) if prior else None
            new_item = (
                after[solution_id].requirement(change.concept_id) if solution_id in after else None
            )
            outcome = _assess(change, old_item, new_item)
            if outcome is None:
                continue
            certainty, reason, action = outcome
            if stage == "proposed_adoption" and certainty == "confirmed":
                certainty = "potential"
            evidence = tuple(
                value
                for value in (
                    old_item.decision_location if old_item else None,
                    f"exception:{old_item.exception.decision_id}"
                    if old_item and old_item.exception
                    else None,
                    f"architecture-revision:{plan.revision_id}",
                )
                if value
            )
            items.append(
                ImpactItem(
                    id=stable_id(
                        "change-impact",
                        old[1],
                        new[1],
                        solution_id,
                        plan.revision_id,
                        change.concept_id,
                        change.kind,
                    ),
                    change=change,
                    solution_id=solution_id,
                    revision_id=plan.revision_id,
                    solution=plan.name,
                    version=plan.version,
                    owner=plan.target.owner,
                    team=plan.target.team,
                    repository=plan.target.repository,
                    applicability_before=old_item.applicability if old_item else None,
                    applicability_after=new_item.applicability if new_item else None,
                    certainty=certainty,
                    reason=reason,
                    action=action,
                    evidence=evidence,
                    gaps=_routing_gaps(plan),
                )
            )
    routes: dict[str, list[ImpactItem]] = defaultdict(list)
    for item in items:
        routes[item.repository or item.team or item.owner or "unrouted"].append(item)
    drafts = tuple(
        ImpactDraft(
            route=route,
            title=(
                f"Baseline change {old[0].id}@{old[0].version} -> {new[0].id}@{new[0].version}: "
                f"{len(group)} item(s) to review"
            ),
            body="\n".join(
                f"- [{item.certainty}] {item.solution} v{item.version}: {item.change.statement} "
                f"({item.change.kind}). {item.reason} Action: {item.action} "
                f"Ref: {item.id}"
                for item in group
            ),
            items=tuple(item.id for item in group),
        )
        for route, group in sorted(routes.items())
    )
    return ChangeImpactReport(
        stage=stage,
        old_policy=f"{old[0].id}@{old[0].version}",
        new_policy=f"{new[0].id}@{new[0].version}",
        old_revision=old[1],
        new_revision=new[1],
        effective_from=effective_from,
        as_of=as_of,
        changes=tuple(changes),
        items=tuple(items),
        drafts=drafts,
        gaps=tuple(gaps),
    )
