import asyncio
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from ekb.adapters.persistence.postgres.models import TaskRow
from ekb.domain.grounding import GroundingError
from ekb.domain.models import (
    AuthorityEvent,
    Candidate,
    ConceptKind,
    ConceptVersion,
    Extraction,
    InferenceResult,
    ProcessingTask,
    ProposedRelationship,
    RelationshipKind,
    Segment,
    SemanticVersion,
    SourceItem,
    SourceRecord,
    SourceRevision,
    Usage,
    content_hash,
    now,
    stable_id,
)

pytestmark = pytest.mark.integration


async def seed(database):
    source_id, revision_id = uuid4(), uuid4()
    segment = Segment(
        id=uuid4(),
        revision_id=revision_id,
        ordinal=0,
        text="Users must authenticate. Authentication depends on identity management.",
    )
    async with database() as uow:
        await uow.sources.save(
            SourceRecord(
                item=SourceItem(
                    id=source_id, connector="test", location="urn:test", name="Test", format="txt"
                )
            )
        )
        revision = SourceRevision(
            id=revision_id,
            source_id=source_id,
            content_hash=content_hash(segment.text.encode()),
            artifact_key="test",
        )
        await uow.sources.save_revision(revision)
        await uow.sources.save_segments(revision.id, [segment], "parser-v1")
        await uow.commit()
    return segment


async def test_unit_of_work_requires_explicit_commit(database):
    item = SourceItem(id=uuid4(), connector="test", location="urn:test", name="Test", format="txt")
    async with database() as uow:
        await uow.sources.save(SourceRecord(item=item))
    async with database() as uow:
        assert await uow.sources.get(item.id) is None


async def test_repository_evidence_contract_and_idempotency(database):
    segment = await seed(database)
    candidate = Candidate(
        kind=ConceptKind.REQUIREMENT,
        statement="Users must authenticate.",
        excerpt="Users must authenticate.",
    )
    result = InferenceResult(
        extraction=Extraction(candidates=[candidate]), model_id="test", prompt_version="v1"
    )
    async with database() as uow:
        ids = await uow.knowledge.persist(segment, result)
        await uow.knowledge.persist(segment, result)
        await uow.commit()
    async with database() as uow:
        concept = await uow.knowledge.get(ids[0])
        assert concept and len(concept.versions) == 1 and len(concept.occurrences) == 1
        assert len(await uow.knowledge.provenance(ids[0])) == 1
        assert await uow.sources.segments(segment.revision_id, "missing-parser") is None
        with pytest.raises(GroundingError):
            await uow.knowledge.persist(
                segment,
                result.model_copy(
                    update={
                        "extraction": Extraction(
                            candidates=[
                                candidate.model_copy(update={"excerpt": "Hallucinated evidence."})
                            ]
                        )
                    }
                ),
            )


async def test_recursive_lineage_handles_cycles_and_reverse_edges(database):
    segment = await seed(database)
    first = Candidate(
        kind=ConceptKind.REQUIREMENT, statement="Authentication", excerpt="Users must authenticate."
    )
    second = Candidate(
        kind=ConceptKind.REQUIREMENT,
        statement="Identity management",
        excerpt="identity management.",
    )
    evidence = "Authentication depends on identity management."
    proposals = [
        ProposedRelationship(
            from_index=0, to_index=1, kind=RelationshipKind.DEPENDS_ON, excerpt=evidence
        ),
        ProposedRelationship(
            from_index=1, to_index=0, kind=RelationshipKind.DERIVES_FROM, excerpt=evidence
        ),
    ]
    async with database() as uow:
        ids = await uow.knowledge.persist(
            segment,
            InferenceResult(
                extraction=Extraction(candidates=[first, second], relationships=proposals),
                model_id="test",
                prompt_version="v1",
            ),
        )
        await uow.commit()
    async with database() as uow:
        assert await uow.lineage.trace_origins(ids[0]) == [ids[1]]
        assert await uow.lineage.find_dependents(ids[0]) == [ids[1]]
        assert await uow.lineage.trace_origins(uuid4()) == []
        concept = await uow.knowledge.get(ids[0])
        assert concept and all(edge.status == "proposed" for edge in concept.relationships)


async def test_explicit_version_and_authority_history(database):
    segment = await seed(database)
    async with database() as uow:
        ids = await uow.knowledge.persist(
            segment,
            InferenceResult(
                extraction=Extraction(
                    candidates=[
                        Candidate(
                            kind=ConceptKind.REQUIREMENT,
                            statement="Authenticate",
                            excerpt="authenticate.",
                        )
                    ]
                ),
                model_id="test",
                prompt_version="v1",
            ),
        )
        await uow.commit()
    async with database() as uow:
        detail = await uow.knowledge.get(ids[0])
        assert detail is not None
        old = detail.versions[0]
        revision = ConceptVersion(
            id=uuid4(),
            concept_id=ids[0],
            predecessor_id=old.id,
            version=SemanticVersion.parse("1.0.1"),
            candidate=old.candidate,
        )
        await uow.knowledge.add_version(revision)
        await uow.knowledge.record_authority(
            AuthorityEvent(
                id=uuid4(),
                concept_version_id=revision.id,
                action="promote",
                actor="test-reviewer",
                reason="Explicit test approval",
                effective_at=date(2026, 1, 1),
            )
        )
        await uow.commit()
    async with database() as uow:
        detail = await uow.knowledge.get(ids[0])
        assert detail is not None
        assert len(detail.versions) == 2 and detail.versions[0] == old
        assert detail.versions[1].predecessor_id == old.id
        assert len(detail.authority_events) == 1


async def test_expired_lease_can_be_reclaimed_but_wrong_owner_is_fenced(database):
    segment = await seed(database)
    task = ProcessingTask(
        id=stable_id(segment.revision_id, "fp"), revision_id=segment.revision_id, fingerprint="fp"
    )
    owner, successor = uuid4(), uuid4()
    async with database() as uow:
        assert await uow.processing.claim(task, owner)
        await uow.commit()
    async with database() as uow:
        assert not await uow.processing.claim(task, successor)
        await uow.session.execute(
            update(TaskRow)
            .where(TaskRow.id == task.id)
            .values(lease_until=now() - timedelta(seconds=1))
        )
        await uow.commit()
    async with database() as uow:
        assert await uow.processing.claim(task, successor)
        await uow.processing.accumulate_usage(task.id, successor, Usage(input_tokens=10))
        await uow.commit()
    async with database() as uow:
        with pytest.raises(RuntimeError, match="lease lost"):
            await uow.processing.heartbeat(task.id, owner)
    async with database() as uow:
        await uow.processing.finish(task.model_copy(update={"status": "done"}), successor)
        await uow.commit()
    async with database() as uow:
        stored = await uow.processing.task(segment.revision_id, "fp")
        assert stored and stored.status == "done" and stored.usage.input_tokens == 10
        assert not await uow.processing.claim(task, owner)


async def test_concurrent_repository_claim_contract(database):
    segment = await seed(database)
    task = ProcessingTask(id=uuid4(), revision_id=segment.revision_id, fingerprint="fp")

    async def claim():
        async with database() as uow:
            claimed = await uow.processing.claim(task, uuid4())
            await uow.commit()
            return claimed

    assert sum(await asyncio.gather(claim(), claim(), claim())) == 1
    async with database() as uow:
        assert len(list(await uow.session.scalars(select(TaskRow)))) == 1
