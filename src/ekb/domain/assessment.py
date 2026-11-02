from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from ekb.domain.models import Model, Scope, fingerprint, stable_id
from ekb.domain.resolution import applicable

type Applicability = Literal["applicable", "not_applicable", "unknown"]
type DesignStatus = Literal["accepted", "violated", "unresolved"]
type VerificationStatus = Literal["passed", "failed", "unknown", "unsupported"]
type AssessmentDisposition = Literal[
    "conforms", "violated", "excepted", "not_applicable", "decision_required", "unknown"
]


class AssessmentTarget(Model):
    solution_id: UUID
    architecture_revision_id: UUID
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Scope
    as_of: date
    owner: str | None = None
    repository: str | None = None
    basis: Literal["planned", "deployed"] = "planned"


class AssessmentReference(Model):
    concept_id: UUID
    version_id: UUID
    statement: str = Field(min_length=1)
    evidence_location: str = Field(min_length=1)


class ImplementationObservation(Model):
    resource_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    check_version: str = Field(min_length=1)
    status: VerificationStatus
    detail: str = Field(min_length=1)
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

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValueError("Exception ends before approval takes effect")
        return self

    def applies(self, target: AssessmentTarget, requirement_version_id: UUID) -> bool:
        return (
            not self.revoked
            and self.solution_id == target.solution_id
            and self.architecture_revision_id == target.architecture_revision_id
            and self.requirement_version_id == requirement_version_id
            and self.effective_from <= target.as_of
            and (self.effective_until is None or target.as_of <= self.effective_until)
            and applicable(self.scope, target.scope, target.as_of)
        )


class RequirementAssessment(Model):
    requirement: AssessmentReference
    applicability: Applicability
    applicability_reason: str = Field(min_length=1)
    authoritative: bool = False
    design: DesignStatus = "unresolved"
    design_reason: str = Field(min_length=1)
    observations: tuple[ImplementationObservation, ...] = ()
    observation_coverage_complete: bool = False
    exception: AssessmentException | None = None
    provenance: tuple[AssessmentReference, ...] = ()


class AssessmentFinding(Model):
    id: UUID
    requirement: AssessmentReference
    disposition: AssessmentDisposition
    applicability: Applicability
    design: DesignStatus
    implementation: VerificationStatus
    explanation: str
    observations: tuple[ImplementationObservation, ...]
    exception: AssessmentException | None = None
    provenance: tuple[AssessmentReference, ...] = ()


class AssessmentCoverage(Model):
    total: int
    applicable: int
    assessed: int
    unresolved: int
    violated: int
    excepted: int
    not_applicable: int


class SolutionAssessment(Model):
    target: AssessmentTarget
    baseline_revision: str
    baseline_complete: bool
    findings: tuple[AssessmentFinding, ...]
    coverage: AssessmentCoverage
    conforms: bool
    acceptable_with_exceptions: bool
    fingerprint: str


def assess_requirement(target: AssessmentTarget, item: RequirementAssessment) -> AssessmentFinding:
    observations = item.observations
    bound = all(
        observation.artifact_sha256 == target.artifact_sha256 and observation.basis == target.basis
        for observation in observations
    )
    implementation: VerificationStatus = "unknown"
    if bound and any(observation.status == "failed" for observation in observations):
        implementation = "failed"
    elif observations and bound and item.observation_coverage_complete:
        if all(observation.status == "passed" for observation in observations):
            implementation = "passed"
        elif any(observation.status == "unsupported" for observation in observations):
            implementation = "unsupported"

    exception = item.exception
    if exception and not exception.applies(target, item.requirement.version_id):
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
        disposition, reason = "violated", "Implementation contradicts an applicable requirement."
    elif implementation == "passed":
        disposition, reason = "conforms", f"Verified {target.basis} implementation."
    else:
        disposition, reason = "unknown", "Implementation evidence is incomplete or unsupported."
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
        applicability=item.applicability,
        design=item.design,
        implementation=implementation,
        explanation=reason,
        observations=observations,
        exception=exception if disposition == "excepted" else None,
        provenance=item.provenance,
    )


def assess_solution(
    target: AssessmentTarget,
    requirements: tuple[RequirementAssessment, ...],
    *,
    baseline_revision: str,
    baseline_complete: bool,
) -> SolutionAssessment:
    if len({item.requirement.concept_id for item in requirements}) != len(requirements):
        raise ValueError("A baseline cannot repeat a requirement identity")
    findings = tuple(assess_requirement(target, item) for item in requirements)
    coverage = AssessmentCoverage(
        total=len(findings),
        applicable=sum(item.applicability == "applicable" for item in findings),
        assessed=sum(item.implementation in {"passed", "failed"} for item in findings),
        unresolved=sum(item.disposition in {"unknown", "decision_required"} for item in findings),
        violated=sum(item.disposition == "violated" for item in findings),
        excepted=sum(item.disposition == "excepted" for item in findings),
        not_applicable=sum(item.disposition == "not_applicable" for item in findings),
    )
    complete = baseline_complete and bool(baseline_revision.strip()) and bool(findings)
    return SolutionAssessment(
        target=target,
        baseline_revision=baseline_revision,
        baseline_complete=baseline_complete,
        findings=findings,
        coverage=coverage,
        conforms=complete
        and all(item.disposition in {"conforms", "not_applicable"} for item in findings),
        acceptable_with_exceptions=complete
        and all(
            item.disposition in {"conforms", "excepted", "not_applicable"} for item in findings
        ),
        fingerprint=fingerprint(
            contract="solution-assessment-v1",
            target=target.model_dump(mode="json"),
            baseline=baseline_revision,
            complete=baseline_complete,
            requirements=[item.model_dump(mode="json") for item in requirements],
        ),
    )
