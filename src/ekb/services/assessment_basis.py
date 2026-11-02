from uuid import UUID

from ekb.domain.architectures import ControlDisposition, SolutionArchitectureRevision
from ekb.domain.assessment import (
    AssessmentException,
    AssessmentReference,
    AssessmentTarget,
    RequirementAssessment,
)
from ekb.domain.baselines import (
    BaselineApproval,
    BaselinePolicy,
    DesignDocument,
    WorkloadContext,
    requirement_applicability,
)
from ekb.domain.models import Model, stable_id
from ekb.domain.resolution import Resolution, applicable
from ekb.services.knowledge_resolver import KnowledgeResolver


class AssessmentBasis(Model):
    target: AssessmentTarget
    baseline_revision: str
    complete: bool
    requirements: tuple[RequirementAssessment, ...]


def canonical_reference(resolution: Resolution, version_id: UUID) -> AssessmentReference | None:
    if not resolution.authoritative or resolution.status != "resolved":
        return None
    version = next((item for item in resolution.versions if item.id == version_id), None)
    if version is None:
        return None
    evidence = next(
        (
            item
            for item in resolution.provenance
            if item.occurrence.concept_version_id == version_id
            and item.occurrence.extraction_metadata.get("purpose") != "relationship"
        ),
        None,
    )
    if evidence is None:
        return None
    return AssessmentReference(
        concept_id=version.concept_id,
        version_id=version.id,
        statement=version.candidate.statement,
        evidence_location=f"occurrence:{evidence.occurrence.id}",
    )


class AssessmentBasisService:
    def __init__(self, resolver: KnowledgeResolver):
        self.resolver = resolver

    async def prepare(
        self,
        policy: BaselinePolicy,
        policy_sha256: str,
        approval: BaselineApproval,
        architecture: SolutionArchitectureRevision,
        design: DesignDocument | None,
        target: AssessmentTarget,
    ) -> AssessmentBasis:
        if (
            target.solution_id != architecture.architecture_id
            or target.architecture_revision_id != architecture.id
            or target.scope != architecture.scope
        ):
            raise ValueError("Assessment target must match the registered architecture")
        root_resolution = await self.resolver.resolve_concept(
            approval.concept_id, target.scope, as_of=target.as_of
        )
        root = canonical_reference(root_resolution, approval.version_id)
        root_bound = bool(
            root
            and root.concept_id == approval.concept_id
            and applicable(policy.scope, target.scope, target.as_of)
            and any(
                item.occurrence.concept_version_id == approval.version_id
                and item.revision.content_hash == policy_sha256
                and item.occurrence.extraction_metadata.get("purpose") != "relationship"
                for item in root_resolution.provenance
            )
        )
        complete = root_bound
        controls = {item.concept_id: item for item in architecture.controls}
        decisions = {item.concept_id: item for item in design.decisions} if design else {}
        requirements = []
        for rule in policy.requirements:
            resolution = await self.resolver.resolve_concept(
                rule.concept_id, target.scope, as_of=target.as_of
            )
            reference = canonical_reference(resolution, rule.version_id)
            authoritative = bool(
                root_bound and reference and reference.concept_id == rule.concept_id
            )
            complete = complete and authoritative
            if reference is None:
                reference = AssessmentReference(
                    concept_id=rule.concept_id,
                    version_id=rule.version_id,
                    statement="Pinned requirement authority is not established.",
                    evidence_location=f"version:{rule.version_id}",
                )
            status, reason = requirement_applicability(
                rule, target.scope, design.context if design else WorkloadContext()
            )
            if (
                rule.scope.effective_from
                and target.as_of < rule.scope.effective_from
                or rule.scope.effective_to
                and target.as_of > rule.scope.effective_to
            ):
                status, reason = "not_applicable", "Outside the approved applicability period."
            control = controls.get(rule.concept_id)
            decision = decisions.get(rule.concept_id)
            exception = None
            item = RequirementAssessment(
                requirement=reference,
                authoritative=authoritative,
                applicability=status,
                applicability_reason=reason,
                design="unresolved",
                design_reason="A required, version-bound solution decision is missing.",
                provenance=(root, reference) if root and root_bound else (reference,),
            )
            if control and control.concept_version_id == rule.version_id:
                if decision and decision.version_id == rule.version_id:
                    permitted = decision.option in {option.id for option in rule.options}
                    item = item.model_copy(
                        update={
                            "design": "accepted" if permitted else "violated",
                            "design_reason": decision.rationale
                            if permitted
                            else "The documented option is not permitted by the approved baseline.",
                        }
                    )
                if control.disposition == ControlDisposition.EXCEPTION:
                    assert control.approved_at and control.approved_by and control.rationale
                    exception = AssessmentException(
                        decision_id=stable_id(
                            "architecture-exception", architecture.id, rule.version_id
                        ),
                        solution_id=architecture.architecture_id,
                        architecture_revision_id=architecture.id,
                        requirement_version_id=rule.version_id,
                        scope=architecture.scope,
                        rationale=control.rationale,
                        approved_by=control.approved_by,
                        effective_from=control.approved_at.date(),
                        effective_until=control.expires_on,
                    )
            requirements.append(item.model_copy(update={"exception": exception}))
        return AssessmentBasis(
            target=target,
            baseline_revision=policy_sha256,
            complete=complete,
            requirements=tuple(requirements),
        )
