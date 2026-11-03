from collections.abc import Mapping
from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from ekb.domain.architectures import ControlDisposition, SolutionArchitectureRevision
from ekb.domain.assessment import (
    Applicability,
    ApplicabilityBasis,
    AssessmentException,
    AssessmentReference,
    AssessmentTarget,
    CheckRequest,
    DesignStatus,
    ImplementationObservation,
    RequirementAssessment,
)
from ekb.domain.models import Model, Scope, fingerprint, stable_id
from ekb.domain.resolution import overlaps

CONTEXT_FIELDS = (
    "exposure",
    "criticality",
    "data_classification",
    "environment",
    "deployment_boundary",
)
SCOPE_FIELDS = (
    "organization",
    "business_unit",
    "jurisdiction",
    "platform",
    "application",
    "environment",
)


class WorkloadContext(Model):
    exposure: Literal["public", "private"] | None = None
    criticality: Literal["normal", "high"] | None = None
    data_classification: Literal["public", "internal", "confidential", "restricted"] | None = None
    environment: Literal["development", "test", "production"] | None = None
    deployment_boundary: Literal["internet", "private_network", "isolated"] | None = None


class VerificationOption(Model):
    id: str = Field(min_length=1, max_length=100)
    check_id: str = Field(min_length=1, max_length=100)
    resource_type: str = Field(min_length=1, max_length=200)
    expected: str | None = Field(default=None, min_length=1, max_length=1000)


class BaselineRequirement(Model):
    concept_id: UUID
    version_id: UUID
    scope: Scope = Field(default_factory=Scope)
    when: WorkloadContext = Field(default_factory=WorkloadContext)
    excluded_reason: str | None = Field(default=None, min_length=1)
    options: tuple[VerificationOption, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def unique_options(self) -> Self:
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("Baseline options must have distinct IDs")
        return self


class BaselinePolicy(Model):
    id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=1000)
    scope: Scope = Field(default_factory=Scope)
    requirements: tuple[BaselineRequirement, ...] = Field(min_length=1, max_length=200)
    completeness: Literal["explicit_selected_baseline"] = "explicit_selected_baseline"

    @model_validator(mode="after")
    def unique_requirements(self) -> Self:
        if len({item.concept_id for item in self.requirements}) != len(self.requirements):
            raise ValueError("Baseline cannot repeat requirement identities")
        return self


class BaselineApproval(Model):
    concept_id: UUID
    version_id: UUID


class BaselineRegistration(Model):
    """A retained baseline and the canonical adoptions still awaiting a human decision."""

    concept_id: UUID
    version_id: UUID
    policy_id: str
    policy_version: str
    policy_sha256: str
    requirements: int
    pending_approvals: tuple[UUID, ...] = ()


class BaselineSummary(Model):
    """One registered baseline version and whether a person has adopted it canonically."""

    concept_id: UUID
    version_id: UUID
    source: str
    policy_version: str | None
    statement: str
    approved: bool


class ExceptionApproval(Model):
    id: str = Field(min_length=1, max_length=100)
    approved_by: str = Field(min_length=1, max_length=200)
    effective_from: date
    effective_until: date | None = None
    renews: str | None = Field(default=None, min_length=1, max_length=100)
    scope: Scope | None = None

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValueError("Exception ends before approval takes effect")
        if self.renews == self.id:
            raise ValueError("An exception cannot renew itself")
        return self


class DesignDecision(Model):
    """A structured, evidence-bound statement of how a design addresses one requirement."""

    concept_id: UUID
    version_id: UUID
    kind: Literal["implements", "disabled", "exception"] = "implements"
    option: str | None = Field(default=None, min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_location: str = Field(min_length=1, max_length=2000)
    resources: tuple[str, ...] = Field(default=(), max_length=100)
    exception: ExceptionApproval | None = None

    @model_validator(mode="after")
    def valid_kind(self) -> Self:
        if self.kind == "implements" and not self.option:
            raise ValueError("An implementing decision must select a baseline option")
        if (self.kind == "exception") != (self.exception is not None):
            raise ValueError("Only exception decisions carry, and must carry, an approval")
        return self


class SolutionIdentity(Model):
    name: str | None = Field(default=None, max_length=200)
    owner: str | None = Field(default=None, max_length=200)
    team: str | None = Field(default=None, max_length=200)
    repository: str | None = Field(default=None, max_length=2000)
    environment: str | None = Field(default=None, max_length=100)


class DesignDocument(Model):
    solution: SolutionIdentity = Field(default_factory=SolutionIdentity)
    context: WorkloadContext
    # Duplicate decisions are allowed: conflicting passages become unresolved, not a winner.
    decisions: tuple[DesignDecision, ...] = Field(default=(), max_length=200)


def requirement_applicability(
    requirement: BaselineRequirement,
    scope: Scope,
    context: WorkloadContext,
    as_of: date | None = None,
) -> tuple[Applicability, ApplicabilityBasis, str]:
    if not overlaps(requirement.scope, scope):
        return (
            "not_applicable",
            "scope_excluded",
            "The approved requirement scope excludes this target.",
        )
    if as_of and (
        requirement.scope.effective_from
        and as_of < requirement.scope.effective_from
        or requirement.scope.effective_to
        and as_of > requirement.scope.effective_to
    ):
        return "not_applicable", "period_excluded", "Outside the approved applicability period."
    # Unknown target facets are not evidence that a scoped requirement is inapplicable.
    for field in SCOPE_FIELDS:
        if getattr(requirement.scope, field) and not getattr(scope, field):
            return "unknown", "scope_unknown", f"Target scope is missing {field}."
    for field in CONTEXT_FIELDS:
        required, actual = getattr(requirement.when, field), getattr(context, field)
        if required and actual and required != actual:
            return (
                "not_applicable",
                "condition_false",
                f"The approved condition {field}={required} does not match {actual}.",
            )
    for field in CONTEXT_FIELDS:
        if getattr(requirement.when, field) and not getattr(context, field):
            return "unknown", "context_unknown", f"Workload context is missing {field}."
    if requirement.excluded_reason:
        return "not_applicable", "tailored", f"Tailored out: {requirement.excluded_reason}"
    return "applicable", "selected", "Included by the explicitly selected enterprise baseline."


def _design(
    rule: BaselineRequirement, decisions: list[DesignDecision]
) -> tuple[DesignStatus, str, DesignDecision | None]:
    current = [item for item in decisions if item.version_id == rule.version_id]
    stale = [item for item in decisions if item.version_id != rule.version_id]
    positions = {(item.kind, item.option, item.resources) for item in current}
    if len(positions) > 1:
        locations = ", ".join(item.evidence_location for item in current)
        return "unresolved", f"Conflicting design decisions at {locations}.", None
    if not current:
        if stale:
            return (
                "unresolved",
                "The design decision is bound to a different requirement version.",
                None,
            )
        return "unresolved", "A required, version-bound solution decision is missing.", None
    decision = current[0]
    if decision.kind == "disabled":
        return (
            "violated",
            "The design intentionally departs from the requirement without an approved "
            f"exception ({decision.evidence_location}).",
            decision,
        )
    if decision.option not in {option.id for option in rule.options}:
        return (
            "violated",
            f"The documented option {decision.option!r} is not permitted by the approved baseline.",
            decision,
        )
    return "accepted", decision.rationale, decision


def _exception(
    decisions: list[DesignDecision],
    architecture: SolutionArchitectureRevision,
    rule: BaselineRequirement,
) -> AssessmentException | None:
    approvals = sorted(
        (item for item in decisions if item.exception),
        key=lambda item: item.exception.effective_from if item.exception else date.min,
    )
    if approvals:
        decision = approvals[-1]
        assert decision.exception
        approval = decision.exception
        return AssessmentException(
            decision_id=stable_id("design-exception", architecture.architecture_id, approval.id),
            renews=stable_id("design-exception", architecture.architecture_id, approval.renews)
            if approval.renews
            else None,
            solution_id=architecture.architecture_id,
            architecture_revision_id=architecture.id,
            requirement_version_id=decision.version_id,
            scope=approval.scope or architecture.scope,
            rationale=decision.rationale,
            approved_by=approval.approved_by,
            effective_from=approval.effective_from,
            effective_until=approval.effective_until,
            evidence_location=decision.evidence_location,
        )
    control = next(
        (item for item in architecture.controls if item.concept_id == rule.concept_id), None
    )
    if control is None or control.disposition != ControlDisposition.EXCEPTION:
        return None
    assert control.approved_at and control.approved_by and control.rationale
    return AssessmentException(
        decision_id=stable_id(
            "architecture-exception", architecture.id, control.concept_version_id
        ),
        solution_id=architecture.architecture_id,
        architecture_revision_id=architecture.id,
        requirement_version_id=control.concept_version_id or rule.version_id,
        scope=architecture.scope,
        rationale=control.rationale,
        approved_by=control.approved_by,
        effective_from=control.approved_at.date(),
        effective_until=control.expires_on,
        evidence_location=f"architecture:{architecture.id}",
    )


def plan_requirements(
    policy: BaselinePolicy,
    references: Mapping[UUID, AssessmentReference | None],
    root: AssessmentReference | None,
    architecture: SolutionArchitectureRevision,
    design: DesignDocument | None,
    target: AssessmentTarget,
) -> tuple[RequirementAssessment, ...]:
    """Plan every policy requirement, independent of the author's architecture checklist."""
    context = design.context if design else WorkloadContext()
    requirements: list[RequirementAssessment] = []
    for rule in policy.requirements:
        reference = references.get(rule.concept_id)
        authoritative = bool(root and reference and reference.concept_id == rule.concept_id)
        if reference is None:
            reference = AssessmentReference(
                concept_id=rule.concept_id,
                version_id=rule.version_id,
                statement="Pinned requirement authority is not established.",
                evidence_location=f"version:{rule.version_id}",
            )
        status, basis, reason = requirement_applicability(rule, target.scope, context, target.as_of)
        decisions = [
            item
            for item in (design.decisions if design else ())
            if item.concept_id == rule.concept_id
        ]
        design_status, design_reason, decision = _design(
            rule, [item for item in decisions if item.kind != "exception"]
        )
        option = next(
            (item for item in rule.options if decision and item.id == decision.option), None
        )
        expected = None
        check = None
        if option and design_status == "accepted" and decision:
            expected = (
                option.expected or f"{option.check_id} passes on every {option.resource_type}"
            )
            check = CheckRequest(
                check_id=option.check_id,
                resource_type=option.resource_type,
                resources=decision.resources,
            )
        requirements.append(
            RequirementAssessment(
                requirement=reference,
                authoritative=authoritative,
                applicability=status,
                applicability_basis=basis,
                applicability_reason=reason,
                design=design_status,
                design_reason=design_reason,
                decision_location=decision.evidence_location if decision else None,
                option=decision.option if decision else None,
                expected=expected,
                check=check,
                exception=_exception(decisions, architecture, rule),
                provenance=(root, reference) if root else (reference,),
            )
        )
    return tuple(requirements)


def with_observations(
    requirements: tuple[RequirementAssessment, ...],
    observations: tuple[tuple[ImplementationObservation, ...], ...],
) -> tuple[RequirementAssessment, ...]:
    """Attach analyzer results aligned with the requirements that requested a check."""
    planned = [item for item in requirements if item.check]
    if len(planned) != len(observations):
        raise ValueError("Observations must align with planned checks")
    results = iter(observations)
    updated: list[RequirementAssessment] = []
    for item in requirements:
        if item.check:
            found = next(results)
            item = item.model_copy(
                update={"observations": found, "observation_coverage_complete": bool(found)}
            )
        updated.append(item)
    return tuple(updated)


def basis_gaps(design: DesignDocument | None, target: AssessmentTarget) -> tuple[str, ...]:
    """Missing routing identity is disclosed; it never changes a requirement disposition."""
    gaps: list[str] = []
    if design is None:
        gaps.append("The architecture artefact is not a machine-readable design document.")
    for field, label in (
        ("owner", "owner"),
        ("team", "responsible team"),
        ("repository", "repository"),
    ):
        if not getattr(target, field):
            gaps.append(f"The solution {label} is not recorded.")
    return tuple(gaps)


def basis_fingerprint(
    policy_sha256: str,
    approval: BaselineApproval,
    design: DesignDocument | None,
    target: AssessmentTarget,
    references: Mapping[UUID, AssessmentReference | None],
) -> str:
    """Changes to the baseline, context, decisions, versions or implementation invalidate it."""
    return fingerprint(
        contract="assessment-basis-v1",
        policy=policy_sha256,
        approval=approval.model_dump(mode="json"),
        design=design.model_dump(mode="json") if design else None,
        target=target.model_dump(mode="json"),
        references={
            str(key): value.model_dump(mode="json") if value else None
            for key, value in sorted(references.items(), key=lambda item: str(item[0]))
        },
    )
