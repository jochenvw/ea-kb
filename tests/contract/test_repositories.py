import asyncio
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from ekb.adapters.persistence.postgres.models import TaskRow, VersionRow
from ekb.domain.grounding import GroundingError
from ekb.domain.models import (
    AuthorityEvent,
    Candidate,
    ConceptVersion,
    Extraction,
    InferenceResult,
    KnowledgeLevel,
    ProcessingTask,
    ProposedRelationship,
    RelationshipKind,
    Scope,
    Segment,
    SemanticType,
    SemanticVersion,
    SourceItem,
    SourceRecord,
    SourceRevision,
    Usage,
    content_hash,
    now,
    stable_id,
)
from ekb.services.knowledge import KnowledgeService

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


async def test_levels_preserve_goals_and_filter_platform_requirements_before_paging(database):
    segment = await seed(database)
    candidates = [
        Candidate(
            semantic_type=kind,
            scope=Scope(platform=platform),
            statement="Users must authenticate.",
            excerpt="Users must authenticate.",
            source_key=f"level-{index}",
        )
        for index, (kind, platform) in enumerate(
            (
                (SemanticType.PLATFORM_REQUIREMENT, "Azure"),
                (SemanticType.TECHNICAL_REQUIREMENT, None),
                (None, " "),
                (None, "\t\n"),
                (SemanticType.STRATEGIC_GOAL, "Azure"),
            )
        )
    ]
    async with database() as uow:
        ids = await uow.knowledge.persist(
            segment,
            InferenceResult(
                extraction=Extraction(candidates=candidates),
                model_id="test",
                prompt_version="levels",
            ),
        )
        await uow.commit()
    async with database() as uow:
        platform = await uow.knowledge.search(level=KnowledgeLevel.IMPLEMENTATION)
        assert [row.id for row in platform] == [ids[0]]
        first = await uow.knowledge.search(level=KnowledgeLevel.TOP_LEVEL, limit=1)
        remaining = await uow.knowledge.search(level=KnowledgeLevel.TOP_LEVEL, after=first[0].id)
        assert {row.id for row in first + remaining} == set(ids[1:])
        for row in platform + first + remaining:
            assert row.matching_version_id
            assert row.levels[row.matching_version_id] == (
                KnowledgeLevel.IMPLEMENTATION if row.id == ids[0] else KnowledgeLevel.TOP_LEVEL
            )


async def test_repository_evidence_contract_and_idempotency(database):
    segment = await seed(database)
    candidate = Candidate(
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
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
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
        statement="Authentication",
        excerpt="Users must authenticate.",
    )
    second = Candidate(
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
        statement="Identity management",
        excerpt="identity management.",
    )
    evidence = "Authentication depends on identity management."
    proposals = [
        ProposedRelationship(
            from_index=0, to_index=1, kind=RelationshipKind.DEPENDS_ON, excerpt=evidence
        ),
        ProposedRelationship(
            from_index=1, to_index=0, kind=RelationshipKind.REFINES, excerpt=evidence
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


async def test_assertions_are_replayable_projections_not_edits_to_originals(database):
    from ekb.domain.assertions import assertion_fingerprint
    from ekb.domain.models import Assertion
    from ekb.services.assertions import AssertionService

    segment = await seed(database)
    following = segment.model_copy(
        update={
            "id": uuid4(),
            "ordinal": 1,
            "text": "Authentication prevents unauthenticated access.",
        }
    )
    async with database() as uow:
        await uow.sources.save_segments(segment.revision_id, [segment, following], "parser-v1")
        ids = await uow.knowledge.persist(
            segment,
            InferenceResult(
                extraction=Extraction(
                    candidates=[
                        Candidate(
                            semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
                            statement="Users must authenticate.",
                            excerpt="Users must authenticate.",
                        )
                    ]
                ),
                model_id="test",
                prompt_version="v1",
            ),
        )
        await uow.commit()
        original = await uow.knowledge.get(ids[0])
        original_revision = (await uow.resolution.snapshot()).revision
    assert original

    class Writer:
        fingerprint = "test-phrasing-v1"
        calls = 0

        async def rewrite(self, version, evidence):
            self.calls += 1
            return Assertion(
                text="Users must authenticate.",
                uncertainty=None,
                fingerprint=assertion_fingerprint(version, evidence, self.fingerprint),
                occurrence_id=evidence.occurrence.id,
                source_id=evidence.source.item.id,
                model_id="test",
                prompt_version="v1",
            )

    writer = Writer()
    service = AssertionService(database, writer)
    assert (await service.generate(concurrency=2)).generated == 1
    assert (await service.generate(concurrency=2)).reused == 1
    assert writer.calls == 1
    async with database() as uow:
        detail = await uow.knowledge.get(ids[0])
        assert detail and detail.versions[0].assertion
        assert detail.versions[0].candidate == original.versions[0].candidate
        assert detail.occurrences == original.occurrences
        assert detail.authority_events == []
        evidence = (await uow.knowledge.provenance(ids[0]))[0]
        assert evidence.context_after == following and evidence.context_before is None
        assert (await uow.resolution.snapshot()).revision == original_revision
        assertion = detail.versions[0].assertion
        with pytest.raises(ValueError, match="exact version"):
            await uow.knowledge.save_assertion(uuid4(), assertion)
        with pytest.raises(ValueError, match="source binding"):
            await uow.knowledge.save_assertion(
                detail.versions[0].id, assertion.model_copy(update={"source_id": uuid4()})
            )
        await uow.session.execute(
            update(VersionRow)
            .where(VersionRow.id == detail.versions[0].id)
            .values(
                assertion=assertion.model_copy(update={"text": "It must authenticate."}).model_dump(
                    mode="json"
                )
            )
        )
        await uow.commit()
    async with database() as uow:
        legacy = await uow.knowledge.get(ids[0])
        assert legacy and legacy.versions[0].assertion
        assert legacy.versions[0].assertion.text is None
        assert legacy.versions[0].assertion.uncertainty
        assert legacy.versions[0].candidate == original.versions[0].candidate


async def test_explicit_version_and_authority_history(database):
    segment = await seed(database)
    async with database() as uow:
        ids = await uow.knowledge.persist(
            segment,
            InferenceResult(
                extraction=Extraction(
                    candidates=[
                        Candidate(
                            semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
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
            semantic_version=SemanticVersion.parse("1.0.1"),
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
        assert any(
            state.concept_version_id == revision.id and state.canonical_status == "canonical"
            for state in detail.authority_states
        )


async def test_concurrent_prerelease_branches_preserve_unknown_and_final_history(database):
    segment = await seed(database)
    async with database() as uow:
        ids = await uow.knowledge.persist(
            segment,
            InferenceResult(
                extraction=Extraction(
                    candidates=[
                        Candidate(
                            semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
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
        assert old.semantic_version is None
        branches = [
            ConceptVersion(
                id=uuid4(),
                concept_id=ids[0],
                predecessor_id=old.id,
                semantic_version=SemanticVersion.parse(label),
                candidate=old.candidate,
            )
            for label in ("2.0.0-draft.1", "2.0.0-preview.2")
        ]
        for branch in branches:
            await uow.knowledge.add_version(branch)
        final = branches[0].model_copy(
            update={
                "id": uuid4(),
                "predecessor_id": branches[0].id,
                "semantic_version": SemanticVersion.parse("2.0.0"),
            }
        )
        await uow.knowledge.add_version(final)
        with pytest.raises(ValueError, match="exceed"):
            await uow.knowledge.add_version(
                final.model_copy(
                    update={
                        "id": uuid4(),
                        "predecessor_id": final.id,
                        "semantic_version": SemanticVersion.parse("2.0.0+build.2"),
                    }
                )
            )
        await uow.commit()
    async with database() as uow:
        detail = await uow.knowledge.get(ids[0])
        assert detail and len(detail.versions) == 4
        assert all(state.canonical_status == "not_canonical" for state in detail.authority_states)
        assert {
            version.predecessor_id
            for version in detail.versions
            if version.id in {branch.id for branch in branches}
        } == {old.id}


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


async def test_usage_retains_model_calls_and_unknown_cost_through_resume(database):
    segment = await seed(database)
    task = ProcessingTask(id=uuid4(), revision_id=segment.revision_id, fingerprint="usage")
    owner, successor = uuid4(), uuid4()
    async with database() as uow:
        assert await uow.processing.claim(task, owner)
        await uow.processing.accumulate_usage(task.id, owner, Usage(model_calls=1, cost=0))
        await uow.commit()
    stats = await KnowledgeService(database).statistics()
    assert stats.configured_cost == 0 and stats.model_calls == 1 and not stats.tasks_truncated

    async with database() as uow:
        await uow.processing.accumulate_usage(
            task.id, owner, Usage(input_tokens=10, model_calls=2, cost=0.25)
        )
        await uow.processing.finish(task.model_copy(update={"status": "failed"}), owner)
        await uow.commit()
    async with database() as uow:
        stored = await uow.processing.task(segment.revision_id, "usage")
        assert stored and stored.usage.cost == pytest.approx(0.25)
        assert stored.usage.model_calls == 3
    assert (await KnowledgeService(database).statistics()).model_calls == 3
    async with database() as uow:
        assert await uow.processing.claim(task, successor)
        await uow.processing.accumulate_usage(task.id, successor, Usage(model_calls=1))
        await uow.processing.accumulate_usage(
            task.id, successor, Usage(output_tokens=5, model_calls=1, cost=0.5)
        )
        await uow.processing.finish(task.model_copy(update={"status": "done"}), successor)
        await uow.commit()

    other = ProcessingTask(id=uuid4(), revision_id=segment.revision_id, fingerprint="priced")
    async with database() as uow:
        assert await uow.processing.claim(other, owner)
        await uow.processing.accumulate_usage(other.id, owner, Usage(model_calls=1, cost=0.2))
        await uow.processing.finish(other.model_copy(update={"status": "done"}), owner)
        await uow.commit()
    async with database() as uow:
        stored = await uow.processing.task(segment.revision_id, "usage")
        assert stored and stored.attempts == 2 and stored.usage.cost is None
        assert stored.usage.model_calls == 5
        assert stored.usage.input_tokens == 10 and stored.usage.output_tokens == 5
    stats = await KnowledgeService(database).statistics()
    assert stats.configured_cost is None and stats.model_calls == 6

    legacy = ProcessingTask(id=uuid4(), revision_id=segment.revision_id, fingerprint="legacy")
    async with database() as uow:
        assert await uow.processing.claim(legacy, owner)
        await uow.processing.accumulate_usage(legacy.id, owner, Usage(input_tokens=10, cost=0.1))
        await uow.processing.finish(legacy.model_copy(update={"status": "failed"}), owner)
        await uow.commit()
    legacy_stats = await KnowledgeService(database).statistics()
    assert legacy_stats.configured_cost is None and legacy_stats.model_calls is None
    async with database() as uow:
        assert await uow.processing.claim(legacy, successor)
        await uow.processing.accumulate_usage(legacy.id, successor, Usage(model_calls=1, cost=0.1))
        await uow.processing.finish(legacy.model_copy(update={"status": "done"}), successor)
        await uow.commit()
    async with database() as uow:
        recorded = await uow.processing.task(segment.revision_id, "legacy")
        assert recorded and recorded.usage.cost is None

    empty = ProcessingTask(id=uuid4(), revision_id=segment.revision_id, fingerprint="empty")
    async with database() as uow:
        assert await uow.processing.claim(empty, owner)
        await uow.processing.accumulate_usage(empty.id, owner, Usage())
        await uow.processing.accumulate_usage(empty.id, owner, Usage(model_calls=1, cost=0.3))
        await uow.processing.finish(empty.model_copy(update={"status": "done"}), owner)
        await uow.commit()
    async with database() as uow:
        recorded = await uow.processing.task(segment.revision_id, "empty")
        assert recorded and recorded.usage.model_calls == 1 and recorded.usage.cost is None

    retry = ProcessingTask(id=uuid4(), revision_id=segment.revision_id, fingerprint="no-usage")
    async with database() as uow:
        assert await uow.processing.claim(retry, owner)
        await uow.processing.finish(retry.model_copy(update={"status": "failed"}), owner)
        await uow.commit()
    async with database() as uow:
        assert await uow.processing.claim(retry, successor)
        await uow.processing.accumulate_usage(retry.id, successor, Usage(model_calls=1, cost=0.4))
        await uow.processing.finish(retry.model_copy(update={"status": "done"}), successor)
        await uow.commit()
    async with database() as uow:
        recorded = await uow.processing.task(segment.revision_id, "no-usage")
        assert recorded and recorded.usage.cost == pytest.approx(0.4)


async def test_processing_task_pages_and_usage_flag_truncated_results(database):
    segment = await seed(database)
    tasks = [
        ProcessingTask(
            id=UUID(int=index),
            revision_id=segment.revision_id,
            fingerprint=f"task-{index}",
            status="done",
            attempts=1,
            usage=Usage(model_calls=1, cost=0),
        )
        for index in range(1, 1002)
    ]
    async with database() as uow:
        uow.session.add_all(
            [
                TaskRow(
                    id=task.id,
                    revision_id=task.revision_id,
                    fingerprint=task.fingerprint,
                    status=task.status,
                    attempts=task.attempts,
                    payload=task.model_dump(mode="json"),
                )
                for task in tasks
            ]
        )
        await uow.commit()
    async with database() as uow:
        first = await uow.processing.tasks(limit=2)
        second = await uow.processing.tasks(limit=2, after=first[-1].id)
        last = await uow.processing.tasks(limit=2, after=UUID(int=1000))
        assert [task.id for task in first + second] == [task.id for task in tasks[:4]]
        assert [task.id for task in last] == [tasks[-1].id]
        assert not await uow.processing.tasks(limit=2, after=last[-1].id)
    stats = await KnowledgeService(database).statistics()
    assert stats.task_count == stats.model_calls == 1000
    assert stats.tasks_truncated and stats.configured_cost == 0


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
