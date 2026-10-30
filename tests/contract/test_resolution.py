from uuid import uuid4

import pytest

from ekb.domain.execution import Execution
from ekb.domain.models import (
    Candidate,
    Extraction,
    InferenceResult,
    Segment,
    SemanticType,
    SourceItem,
    SourceRecord,
    SourceRevision,
    content_hash,
    fingerprint,
)
from ekb.domain.reconciliation import CachedEmbedding, CachedVerification, SemanticJudgment
from ekb.domain.resolution import Decision, Finding
from ekb.ports import UnitOfWorkFactory
from ekb.services.knowledge_resolver import KnowledgeResolver
from ekb.services.reconciliation_service import ReconciliationService

pytestmark = pytest.mark.integration


async def test_resolution_repository_contract(database: UnitOfWorkFactory):
    source_id, revision_id = uuid4(), uuid4()
    statement = "Synthetic operators must authenticate."
    segment = Segment(id=uuid4(), revision_id=revision_id, ordinal=0, text=statement)
    async with database() as uow:
        await uow.sources.save(
            SourceRecord(
                item=SourceItem(
                    id=source_id,
                    connector="fixture",
                    location="synthetic:contract",
                    name="Synthetic contract",
                    format="txt",
                ),
                revision_id=revision_id,
            )
        )
        await uow.sources.save_revision(
            SourceRevision(
                id=revision_id,
                source_id=source_id,
                content_hash=content_hash(statement.encode()),
                artifact_key="synthetic-contract-original",
            )
        )
        await uow.sources.save_segments(revision_id, [segment], "contract")
        ids = await uow.knowledge.persist(
            segment,
            InferenceResult(
                extraction=Extraction(
                    candidates=[
                        Candidate(
                            semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
                            statement=statement,
                            excerpt=statement,
                        ),
                        Candidate(
                            semantic_type=SemanticType.STRATEGIC_GOAL,
                            statement=statement,
                            excerpt=statement,
                        ),
                    ]
                ),
                model_id="fixture",
                prompt_version="contract",
            ),
        )
        await uow.commit()
        concept = await uow.knowledge.get(ids[0])
        assert concept
    finding = Finding(
        id=uuid4(),
        check_id="contract",
        check_version="1",
        input_revision="source-revision",
        action="recognize_identity",
        concept_ids=(concept.id,),
        occurrence_ids=(concept.occurrences[0].id,),
        confidence=1.0,
        explanation="Synthetic evidence-backed identity recognition.",
    )
    service = ReconciliationService(database)
    await service.propose(finding)
    async with database() as uow:
        stored_source = await uow.sources.get(source_id)
        stored_finding = await uow.resolution.finding(finding.id)
    assert stored_source and stored_source.item.connector == "fixture"
    assert stored_finding and stored_finding.input_revision == "source-revision"
    decision = await service.decide(
        Decision(
            finding_id=finding.id,
            outcome="accepted",
            actor="contract-reviewer",
            rationale="Explicit synthetic review.",
        )
    )
    assert await service.decide(decision) == decision
    assert await ReconciliationService(database).history(finding.id) == [decision]
    with pytest.raises(ValueError, match="Reverse"):
        await service.decide(
            Decision(
                finding_id=finding.id,
                previous_id=decision.id,
                outcome="rejected",
                actor="reviewer",
                rationale="Must explicitly reverse an accepted decision.",
            )
        )
    assert (
        await KnowledgeResolver(database).resolve_concept(concept.id)
    ).status == "not_established"
    assert (await KnowledgeResolver(database).resolve_concept(concept.id, profile="all")).provenance
    results = await KnowledgeResolver(database).resolve_requirement("operators")
    assert len(results) == 1 and results[0].requested_id == concept.id
    unsafe = finding.model_copy(
        update={
            "id": uuid4(),
            "deterministic": True,
            "version_ids": (concept.versions[0].id,),
        }
    )
    await service.propose(unsafe)
    with pytest.raises(ValueError, match="repeated evidence"):
        await service.decide(
            Decision(
                finding_id=unsafe.id,
                outcome="accepted",
                actor="rule:fixture",
                actor_kind="rule",
                policy="exact-identity-v1",
                rationale="One occurrence cannot prove a repeated identity.",
            )
        )


async def test_execution_repository_fences_a_stale_owner(database: UnitOfWorkFactory):
    owner, replacement = uuid4(), uuid4()
    async with database() as uow:
        assert await uow.workflow.lease("knowledge-pipeline", owner)
        await uow.commit()
    async with database() as uow:
        await uow.workflow.release("knowledge-pipeline", owner)
        assert await uow.workflow.lease("knowledge-pipeline", replacement)
        await uow.commit()
    async with database() as uow:
        with pytest.raises(RuntimeError, match="Stale"):
            await uow.workflow.save_execution(
                Execution(
                    kind="pipeline",
                    processor_id="pipeline",
                    version="1",
                    fingerprint="test",
                    owner=owner,
                )
            )


async def test_semantic_replay_repository_commit_validation_and_replacement(
    database: UnitOfWorkFactory,
):
    embedding_key = fingerprint(kind="embedding", input="synthetic")
    judgment_key = fingerprint(kind="verification", input="synthetic")
    embedding = CachedEmbedding(vector=(1.0, 0.0))
    judgment = CachedVerification(
        method="independent.semantic-v1",
        judgment=SemanticJudgment(
            outcome="equivalent",
            confidence=0.9,
            rationale="Grounded synthetic result.",
            old_excerpt="First source.",
            new_excerpt="Second source.",
        ),
    )
    async with database() as uow:
        await uow.workflow.save_semantic_cache({embedding_key: embedding})
    async with database() as uow:
        assert await uow.workflow.semantic_cache([embedding_key]) == {}
        await uow.workflow.save_semantic_cache({embedding_key: embedding, judgment_key: judgment})
        await uow.commit()
    async with database() as uow:
        assert await uow.workflow.semantic_cache([embedding_key, judgment_key]) == {
            embedding_key: embedding,
            judgment_key: judgment,
        }
        with pytest.raises(ValueError):
            await uow.workflow.save_semantic_cache(
                {embedding_key: embedding.model_copy(update={"vector": (0.0, 0.0)})}
            )
        with pytest.raises(ValueError):
            await uow.workflow.save_semantic_cache(
                {judgment_key: judgment.model_copy(update={"status": "failed"})}
            )
        replacement = CachedEmbedding(vector=(0.0, 1.0))
        await uow.workflow.save_semantic_cache({embedding_key: replacement})
        await uow.commit()
    async with database() as uow:
        assert (await uow.workflow.semantic_cache([embedding_key]))[embedding_key] == replacement
