from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ekb.adapters.artifacts import FilesystemArtifactStore
from ekb.domain.architectures import ArchitectureControl, ArchitectureManifest
from ekb.domain.assessment import AssessmentTarget, assess_solution
from ekb.domain.baselines import (
    BaselineApproval,
    BaselinePolicy,
    BaselineRequirement,
    DesignDecision,
    DesignDocument,
    SolutionIdentity,
    VerificationOption,
    WorkloadContext,
)
from ekb.domain.models import (
    Candidate,
    Extraction,
    InferenceResult,
    Scope,
    Segment,
    SemanticType,
    SourceItem,
    SourceRecord,
    SourceRevision,
    content_hash,
    now,
)
from ekb.domain.resolution import Decision
from ekb.services.architectures import ArchitectureService
from ekb.services.assessment_basis import AssessmentBasisService
from ekb.services.knowledge_resolver import KnowledgeResolver
from ekb.services.reconciliation_service import ReconciliationService

pytestmark = pytest.mark.integration


async def seed_requirement(
    database,
    statement,
    raw=None,
    semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
    effective_at=None,
    approve=True,
):
    raw = raw or statement
    source_id, revision_id = uuid4(), uuid4()
    segment = Segment(id=uuid4(), revision_id=revision_id, ordinal=0, text=raw)
    async with database() as uow:
        await uow.sources.save(
            SourceRecord(
                item=SourceItem(
                    id=source_id,
                    connector="fixture",
                    location="urn:assessment",
                    name="Assessment fixture",
                    format="txt",
                ),
                revision_id=revision_id,
            )
        )
        await uow.sources.save_revision(
            SourceRevision(
                id=revision_id,
                source_id=source_id,
                content_hash=content_hash(raw.encode()),
                artifact_key=content_hash(raw.encode()),
            )
        )
        await uow.sources.save_segments(revision_id, [segment], "fixture")
        ids = await uow.knowledge.persist(
            segment,
            InferenceResult(
                extraction=Extraction(
                    candidates=[
                        Candidate(
                            statement=statement,
                            excerpt=statement,
                            semantic_type=semantic_type,
                        )
                    ]
                ),
                model_id="fixture",
                prompt_version="fixture",
            ),
        )
        await uow.commit()
        concept = await uow.knowledge.get(ids[0])
    assert concept
    if not approve:
        return concept
    review = ReconciliationService(database)
    proposal = await review.propose_canonical(concept.id, concept.versions[0].id, Scope())
    await review.decide(
        Decision(
            finding_id=proposal.id,
            outcome="accepted",
            actor="fixture-board",
            rationale="Approve this synthetic requirement and its exact evidence.",
            **({"effective_at": effective_at} if effective_at else {}),
        )
    )
    return concept


@pytest.fixture
async def assessment_setup(database, tmp_path):
    concept = await seed_requirement(database, "Protect stored records.")
    policy = BaselinePolicy(
        id="example",
        version="1",
        statement="Apply the approved records baseline.",
        requirements=(
            BaselineRequirement(
                concept_id=concept.id,
                version_id=concept.versions[0].id,
                when=WorkloadContext(exposure="private"),
                options=(
                    VerificationOption(
                        id="private", check_id="network.private", resource_type="Example/records"
                    ),
                ),
            ),
        ),
    )
    raw = policy.model_dump_json()
    root = await seed_requirement(
        database, policy.statement, raw, SemanticType.STRATEGIC_REQUIREMENT
    )
    approval = BaselineApproval(concept_id=root.id, version_id=root.versions[0].id)
    design = DesignDocument(
        solution=SolutionIdentity(owner="Records", team="Records", repository="example/app"),
        context=WorkloadContext(exposure="private"),
    )
    architectures = ArchitectureService(database, FilesystemArtifactStore(tmp_path))
    architecture = await architectures.register(
        ArchitectureManifest(
            name="Records",
            description="Synthetic reference solution.",
            owner="Records team",
            canonical_source="urn:example:records",
            version="1",
            scope=Scope(application="Records"),
            controls=(ArchitectureControl(concept_id=concept.id),),
        ),
        "design.json",
        "application/json",
        design.model_dump_json().encode(),
    )
    target = AssessmentTarget(
        solution_id=architecture.id,
        architecture_revision_id=architecture.latest.id,
        artifact_sha256="a" * 64,
        scope=architecture.latest.scope,
        as_of=now().date(),
    )
    return policy, approval, architecture.latest, design, target


async def test_basis_detects_omission_and_binds_approval_to_exact_policy(
    database, assessment_setup
):
    policy, approval, architecture, design, target = assessment_setup
    service = AssessmentBasisService(KnowledgeResolver(database))
    digest = content_hash(policy.model_dump_json().encode())
    basis = await service.prepare(policy, digest, approval, architecture, design, target)
    assert basis.complete and len(basis.requirements) == 1
    result = assess_solution(
        target, basis.requirements, baseline_revision=digest, baseline_complete=basis.complete
    )
    assert result.findings[0].disposition == "decision_required"
    assert len(result.findings[0].provenance) == 2
    assert all(
        ref.evidence_location.startswith("occurrence:") for ref in result.findings[0].provenance
    )
    unapproved = await service.prepare(policy, "b" * 64, approval, architecture, design, target)
    assert not unapproved.complete
    assert not unapproved.requirements[0].authoritative


async def test_basis_distinguishes_permitted_alternative_and_unauthorized_intent(
    database, assessment_setup
):
    policy, approval, architecture, design, target = assessment_setup
    rule = policy.requirements[0]
    service = AssessmentBasisService(KnowledgeResolver(database))
    digest = content_hash(policy.model_dump_json().encode())
    for option, expected in (("private", "accepted"), ("public", "violated")):
        decision = DesignDecision(
            concept_id=rule.concept_id,
            version_id=rule.version_id,
            option=option,
            rationale="Intentional design choice.",
            evidence_location="design.md#records",
        )
        updated = design.model_copy(update={"decisions": (decision,)})
        basis = await service.prepare(policy, digest, approval, architecture, updated, target)
        assert basis.requirements[0].design == expected
        assert basis.requirements[0].exception is None


async def test_basis_missing_context_and_wrong_version_remain_unresolved(
    database, assessment_setup
):
    policy, approval, architecture, design, target = assessment_setup
    service = AssessmentBasisService(KnowledgeResolver(database))
    digest = content_hash(policy.model_dump_json().encode())
    missing = design.model_copy(update={"context": WorkloadContext()})
    basis = await service.prepare(policy, digest, approval, architecture, missing, target)
    assert basis.requirements[0].applicability == "unknown"
    public = design.model_copy(update={"context": WorkloadContext(exposure="public")})
    basis = await service.prepare(policy, digest, approval, architecture, public, target)
    assert basis.requirements[0].applicability == "not_applicable"
    wrong = approval.model_copy(update={"version_id": uuid4()})
    assert not (await service.prepare(policy, digest, wrong, architecture, design, target)).complete


async def test_basis_exception_before_approval_is_not_effective(database, assessment_setup):
    policy, approval, architecture, design, target = assessment_setup
    control = architecture.controls[0].model_copy(
        update={
            "disposition": "exception",
            "rationale": "Migration",
            "approved_by": "board",
            "approved_at": datetime(2099, 1, 1, tzinfo=UTC),
        }
    )
    architecture = architecture.model_copy(update={"controls": (control,)})
    service = AssessmentBasisService(KnowledgeResolver(database))
    digest = content_hash(policy.model_dump_json().encode())
    basis = await service.prepare(policy, digest, approval, architecture, design, target)
    result = assess_solution(
        target, basis.requirements, baseline_revision=digest, baseline_complete=basis.complete
    )
    assert result.findings[0].disposition == "decision_required"
