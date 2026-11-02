from ekb.domain.architectures import SolutionArchitectureRevision
from ekb.domain.assessment import AssessmentReference, AssessmentTarget, RequirementAssessment
from ekb.domain.baselines import (
    BaselineApproval,
    BaselinePolicy,
    DesignDocument,
    basis_fingerprint,
    basis_gaps,
    plan_requirements,
)
from ekb.domain.models import Model
from ekb.domain.resolution import Resolution, applicable
from ekb.services.knowledge_resolver import KnowledgeResolver


class AssessmentBasis(Model):
    target: AssessmentTarget
    baseline_revision: str
    completeness: str
    complete: bool
    root_bound: bool = False
    requirements: tuple[RequirementAssessment, ...]
    fingerprint: str
    gaps: tuple[str, ...] = ()


def canonical_reference(resolution: Resolution, version_id) -> AssessmentReference | None:
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
        # The approval binds the exact policy bytes, not merely a similarly named document.
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
        references: dict = {}
        for rule in policy.requirements:
            resolution = await self.resolver.resolve_concept(
                rule.concept_id, target.scope, as_of=target.as_of
            )
            references[rule.concept_id] = canonical_reference(resolution, rule.version_id)
        requirements = plan_requirements(
            policy, references, root if root_bound else None, architecture, design, target
        )
        return AssessmentBasis(
            target=target,
            baseline_revision=policy_sha256,
            completeness=policy.completeness,
            complete=root_bound and all(item.authoritative for item in requirements),
            root_bound=root_bound,
            requirements=requirements,
            fingerprint=basis_fingerprint(policy_sha256, approval, design, target, references),
            gaps=basis_gaps(design, target),
        )
