from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from ekb.domain.architectures import TraceabilityStep
from ekb.domain.models import Model, Scope, fingerprint, stable_id
from ekb.domain.resolution import applicable

type Applicability = Literal["applicable", "not_applicable", "unknown"]
type ApplicabilityBasis = Literal[
    "selected",
    "tailored",
    "condition_false",
    "scope_excluded",
    "period_excluded",
    "context_unknown",
    "scope_unknown",
]
type DesignStatus = Literal["accepted", "violated", "unresolved"]
type VerificationStatus = Literal["passed", "failed", "unknown", "unsupported"]
type AssessmentDisposition = Literal[
    "conforms", "violated", "excepted", "not_applicable", "decision_required", "unknown"
]


class AssessmentTarget(Model):
    solution_id: UUID
    architecture_revision_id: UUID
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parameters_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    commit: str | None = Field(default=None, max_length=200)
    scope: Scope
    as_of: date
    owner: str | None = None
    team: str | None = None
    repository: str | None = None
    basis: Literal["planned", "deployed"] = "planned"


class SolutionAssessmentInput(Model):
    """Allowed inputs for one offline, template-based assessment; nothing is deployed."""

    architecture_id: UUID
    version: str | None = Field(default=None, max_length=100)
    baseline_concept_id: UUID
    baseline_version_id: UUID
    template: dict[str, object]
    parameters: dict[str, object] = Field(default_factory=dict[str, object])
    commit: str | None = Field(default=None, max_length=200)
    as_of: date | None = None
    # Template analysis observes planned configuration only; deployed state needs other evidence.
    basis: Literal["planned"] = "planned"


class AssessmentReference(Model):
    concept_id: UUID
    version_id: UUID
    statement: str = Field(min_length=1)
    evidence_location: str = Field(min_length=1)


class CheckRequest(Model):
    """Binds one selected verification option to every matching resource instance."""

    check_id: str = Field(min_length=1, max_length=100)
    resource_type: str = Field(min_length=1, max_length=200)
    resources: tuple[str, ...] = ()


class ImplementationObservation(Model):
    resource_id: str = Field(min_length=1)
    resource_type: str | None = None
    check_id: str = Field(min_length=1)
    check_version: str = Field(min_length=1)
    status: VerificationStatus
    detail: str = Field(min_length=1)
    expected: str | None = None
    observed: str | None = None
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    location: str = Field(min_length=1)
    basis: Literal["planned", "deployed"] = "planned"


class AssessmentException(Model):
    decision_id: UUID
    solution_id: UUID
    architecture_revision_id: UUID
    requirement_version_id: UUID
    scope: Scope
    rationale: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    effective_from: date
    effective_until: date | None = None
    revoked: bool = False
    renews: UUID | None = None
    evidence_location: str | None = None

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValueError("Exception ends before approval takes effect")
        return self

    def rejection(self, target: AssessmentTarget, requirement_version_id: UUID) -> str | None:
        if self.revoked:
            return "it was revoked"
        if (
            self.solution_id != target.solution_id
            or self.architecture_revision_id != target.architecture_revision_id
        ):
            return "it belongs to another solution or architecture revision"
        if self.requirement_version_id != requirement_version_id:
            return "it is bound to another requirement version"
        if target.as_of < self.effective_from:
            return f"it is not effective until {self.effective_from.isoformat()}"
        if self.effective_until is not None and target.as_of > self.effective_until:
            return f"it expired on {self.effective_until.isoformat()}"
        if not applicable(self.scope, target.scope, target.as_of):
            return "its approved scope excludes this target"
        return None


class RequirementAssessment(Model):
    requirement: AssessmentReference
    applicability: Applicability
    applicability_basis: ApplicabilityBasis = "selected"
    applicability_reason: str = Field(min_length=1)
    authoritative: bool = False
    design: DesignStatus = "unresolved"
    design_reason: str = Field(min_length=1)
    decision_location: str | None = None
    option: str | None = None
    expected: str | None = None
    check: CheckRequest | None = None
    observations: tuple[ImplementationObservation, ...] = ()
    observation_coverage_complete: bool = False
    exception: AssessmentException | None = None
    provenance: tuple[AssessmentReference, ...] = ()


class AssessmentFinding(Model):
    id: UUID
    requirement: AssessmentReference
    disposition: AssessmentDisposition
    authoritative: bool = False
    applicability: Applicability
    applicability_basis: ApplicabilityBasis = "selected"
    design: DesignStatus
    implementation: VerificationStatus
    explanation: str
    remediation: str | None = None
    expected: str | None = None
    decision_location: str | None = None
    resources: tuple[str, ...] = ()
    observations: tuple[ImplementationObservation, ...]
    exception: AssessmentException | None = None
    provenance: tuple[AssessmentReference, ...] = ()
    chain: tuple[TraceabilityStep, ...] = ()


class AssessmentCoverage(Model):
    total: int
    applicable: int
    applicability_unknown: int = 0
    assessed: int
    unresolved: int
    violated: int
    excepted: int
    not_applicable: int
    design_accepted: int = 0
    design_violated: int = 0
    design_unresolved: int = 0
    implementation_passed: int = 0
    implementation_failed: int = 0
    implementation_unknown: int = 0


class SolutionAssessment(Model):
    target: AssessmentTarget
    verification_mode: Literal["verified_observations"] = "verified_observations"
    baseline_revision: str
    baseline_completeness: str = "explicit_selected_baseline"
    baseline_complete: bool
    basis_fingerprint: str | None = None
    findings: tuple[AssessmentFinding, ...]
    coverage: AssessmentCoverage
    design_acceptable: bool = False
    implementation_conforms: bool = False
    conforms: bool
    acceptable_with_exceptions: bool
    gaps: tuple[str, ...] = ()
    fingerprint: str


class RetainedAssessment(Model):
    """An immutable assessment report as produced, keyed by its input fingerprint."""

    id: UUID
    assessed_at: datetime
    baseline_concept_id: UUID
    baseline_version_id: UUID
    report: SolutionAssessment


def _implementation(target: AssessmentTarget, item: RequirementAssessment) -> VerificationStatus:
    observations = item.observations
    bound = all(
        observation.artifact_sha256 == target.artifact_sha256 and observation.basis == target.basis
        for observation in observations
    )
    if bound and any(observation.status == "failed" for observation in observations):
        return "failed"
    if observations and bound and item.observation_coverage_complete:
        if all(observation.status == "passed" for observation in observations):
            return "passed"
        if any(observation.status == "unsupported" for observation in observations):
            return "unsupported"
    return "unknown"


def _remediation(
    disposition: AssessmentDisposition, item: RequirementAssessment, implementation: str
) -> str | None:
    match disposition:
        case "violated" if item.design == "violated":
            return (
                "Change the design to a baseline-permitted option or obtain an approved exception."
            )
        case "violated":
            failed = sorted(
                {obs.resource_id for obs in item.observations if obs.status == "failed"}
            )
            return f"Change the implementation of {', '.join(failed)} to meet: {item.expected}."
        case "decision_required":
            return (
                "Record a version-bound design decision with exact evidence for this requirement."
            )
        case "unknown" if not item.authoritative:
            return "Establish approved enterprise authority for the pinned requirement version."
        case "unknown" if item.applicability == "unknown":
            return "Supply the missing workload context or target scope attribute."
        case "unknown" if implementation == "unsupported":
            return "Add a supported verifier or supply external verification evidence."
        case "unknown":
            return "Supply resolvable implementation evidence covering every relevant resource."
        case _:
            return None


def assess_requirement(target: AssessmentTarget, item: RequirementAssessment) -> AssessmentFinding:
    implementation = _implementation(target, item)
    exception = item.exception
    rejected = None
    if exception:
        rejected = exception.rejection(target, item.requirement.version_id)
        if rejected:
            exception = None
    disposition: AssessmentDisposition
    if not item.authoritative:
        disposition, reason = "unknown", "Applicable enterprise authority is not established."
    elif item.applicability == "unknown":
        disposition, reason = "unknown", item.applicability_reason
    elif item.applicability == "not_applicable":
        disposition, reason = "not_applicable", item.applicability_reason
    elif exception:
        disposition, reason = "excepted", f"Approved exception: {exception.rationale}"
    elif item.design == "violated":
        disposition, reason = "violated", item.design_reason
    elif item.design == "unresolved":
        disposition, reason = "decision_required", item.design_reason
    elif implementation == "failed":
        failed = [obs for obs in item.observations if obs.status == "failed"]
        details = "; ".join(f"{obs.resource_id}: {obs.detail}" for obs in failed)
        disposition, reason = "violated", f"Implementation contradicts the design: {details}"
    elif implementation == "passed":
        disposition, reason = (
            "conforms",
            f"Verified {target.basis} implementation of {len(item.observations)} resource(s).",
        )
    else:
        disposition, reason = "unknown", "Implementation evidence is incomplete or unsupported."
        details = "; ".join(
            f"{obs.resource_id}: {obs.detail}"
            for obs in item.observations
            if obs.status in {"unknown", "unsupported"}
        )
        if details:
            reason = f"{reason} {details}"
    if rejected and item.applicability == "applicable" and item.authoritative:
        reason = f"{reason} The recorded exception does not apply because {rejected}."
    return AssessmentFinding(
        id=stable_id(
            "assessment-finding",
            target.solution_id,
            target.architecture_revision_id,
            item.requirement.version_id,
            target.scope.model_dump(mode="json"),
        ),
        requirement=item.requirement,
        disposition=disposition,
        authoritative=item.authoritative,
        applicability=item.applicability,
        applicability_basis=item.applicability_basis,
        design=item.design,
        implementation=implementation,
        explanation=reason,
        remediation=_remediation(disposition, item, implementation),
        expected=item.expected,
        decision_location=item.decision_location,
        resources=tuple(dict.fromkeys(obs.resource_id for obs in item.observations)),
        observations=item.observations,
        exception=exception if disposition == "excepted" else None,
        provenance=item.provenance,
    )


def assess_solution(
    target: AssessmentTarget,
    requirements: tuple[RequirementAssessment, ...],
    *,
    baseline_revision: str,
    baseline_complete: bool,
    baseline_completeness: str = "explicit_selected_baseline",
    basis_fingerprint: str | None = None,
    gaps: tuple[str, ...] = (),
) -> SolutionAssessment:
    if len({item.requirement.concept_id for item in requirements}) != len(requirements):
        raise ValueError("A baseline cannot repeat a requirement identity")
    findings = tuple(assess_requirement(target, item) for item in requirements)
    applicable_findings = [item for item in findings if item.applicability == "applicable"]
    coverage = AssessmentCoverage(
        total=len(findings),
        applicable=len(applicable_findings),
        applicability_unknown=sum(item.applicability == "unknown" for item in findings),
        assessed=sum(item.implementation in {"passed", "failed"} for item in findings),
        unresolved=sum(item.disposition in {"unknown", "decision_required"} for item in findings),
        violated=sum(item.disposition == "violated" for item in findings),
        excepted=sum(item.disposition == "excepted" for item in findings),
        not_applicable=sum(item.disposition == "not_applicable" for item in findings),
        design_accepted=sum(item.design == "accepted" for item in applicable_findings),
        design_violated=sum(item.design == "violated" for item in applicable_findings),
        design_unresolved=sum(item.design == "unresolved" for item in applicable_findings),
        implementation_passed=sum(item.implementation == "passed" for item in applicable_findings),
        implementation_failed=sum(item.implementation == "failed" for item in applicable_findings),
        implementation_unknown=sum(
            item.implementation in {"unknown", "unsupported"} for item in applicable_findings
        ),
    )
    # Empty or incomplete baselines never pass by vacuous truth.
    complete = baseline_complete and bool(baseline_revision.strip()) and bool(findings)
    decided = complete and all(
        item.authoritative and item.applicability != "unknown" for item in findings
    )
    governed = [item for item in applicable_findings if item.disposition != "excepted"]
    return SolutionAssessment(
        target=target,
        baseline_revision=baseline_revision,
        baseline_completeness=baseline_completeness,
        baseline_complete=baseline_complete,
        basis_fingerprint=basis_fingerprint,
        findings=findings,
        coverage=coverage,
        design_acceptable=decided and all(item.design == "accepted" for item in governed),
        implementation_conforms=decided
        and all(item.implementation == "passed" for item in governed),
        conforms=complete
        and all(item.disposition in {"conforms", "not_applicable"} for item in findings),
        acceptable_with_exceptions=complete
        and all(
            item.disposition in {"conforms", "excepted", "not_applicable"} for item in findings
        ),
        gaps=gaps,
        fingerprint=fingerprint(
            contract="solution-assessment-v2",
            target=target.model_dump(mode="json"),
            baseline=baseline_revision,
            complete=baseline_complete,
            basis=basis_fingerprint,
            requirements=[item.model_dump(mode="json") for item in requirements],
        ),
    )
