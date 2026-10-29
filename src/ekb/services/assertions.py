import asyncio
from uuid import UUID

from ekb.domain.assertions import assertion_fingerprint, validate_assertion
from ekb.domain.models import ConceptVersion, Model
from ekb.observability import log
from ekb.ports import AssertionWriter, UnitOfWorkFactory


class AssertionReport(Model):
    generated: int
    unresolved: int
    reused: int
    failed: int


class AssertionService:
    def __init__(self, uow: UnitOfWorkFactory, writer: AssertionWriter):
        self.uow = uow
        self.writer = writer

    async def generate(
        self, *, source_id: UUID | None = None, concurrency: int = 4
    ) -> AssertionReport:
        if not 1 <= concurrency <= 16:
            raise ValueError("Assertion concurrency must be between 1 and 16")
        queue: asyncio.Queue[tuple[UUID, ConceptVersion] | None] = asyncio.Queue(
            maxsize=concurrency * 2
        )
        counts = {"generated": 0, "unresolved": 0, "reused": 0, "failed": 0}

        async def produce() -> None:
            after = None
            while True:
                async with self.uow() as uow:
                    concepts = await uow.knowledge.search(
                        limit=50, after=after, source_id=source_id
                    )
                if not concepts:
                    break
                for concept in concepts:
                    for version in concept.versions:
                        if source_id is None or any(
                            source.id == source_id and version.id in source.version_ids
                            for source in concept.sources
                        ):
                            await queue.put((concept.id, version))
                after = concepts[-1].id
            for _ in range(concurrency):
                await queue.put(None)

        async def consume() -> None:
            while (item := await queue.get()) is not None:
                concept_id, version = item
                try:
                    async with self.uow() as uow:
                        passages = await uow.knowledge.provenance(concept_id)
                    evidence = min(
                        (
                            p
                            for p in passages
                            if p.occurrence.concept_version_id == version.id
                            and (source_id is None or p.source.item.id == source_id)
                        ),
                        key=lambda p: str(p.occurrence.id),
                    )
                    expected = assertion_fingerprint(version, evidence, self.writer.fingerprint)
                    if version.assertion and version.assertion.fingerprint == expected:
                        counts["reused"] += 1
                        continue
                    assertion = await self.writer.rewrite(version, evidence)
                    validate_assertion(version.candidate, assertion)
                    if (
                        assertion.fingerprint != expected
                        or assertion.occurrence_id != evidence.occurrence.id
                    ):
                        raise ValueError("Assertion input binding mismatch")
                    async with self.uow() as uow:
                        await uow.knowledge.save_assertion(version.id, assertion)
                        await uow.commit()
                    counts["generated" if assertion.text else "unresolved"] += 1
                except Exception as error:
                    counts["failed"] += 1
                    log.warning(
                        "assertion_failed",
                        version_id=str(version.id),
                        error_type=type(error).__name__,
                    )

        async with asyncio.TaskGroup() as group:
            group.create_task(produce())
            for _ in range(concurrency):
                group.create_task(consume())
        return AssertionReport(**counts)
