from uuid import UUID

from ekb.domain.models import (
    REQUIREMENT_TYPES,
    ConceptDetail,
    KnowledgeLevel,
    Model,
    ProcessingTask,
    Provenance,
    total_usage,
)
from ekb.ports import UnitOfWorkFactory


class LineageResult(Model):
    concept_id: UUID
    origins: list[UUID]
    dependents: list[UUID]


class Statistics(Model):
    task_count: int
    tasks_truncated: bool
    failures: int
    input_tokens: int
    output_tokens: int
    model_calls: int | None
    model_latency_ms: float
    configured_cost: float | None
    validation_failures: int


class KnowledgeService:
    def __init__(self, uow: UnitOfWorkFactory):
        self.uow = uow

    async def search(
        self,
        query: str = "",
        limit: int = 50,
        *,
        after: UUID | None = None,
        source_id: UUID | None = None,
        level: KnowledgeLevel | None = None,
    ) -> list[ConceptDetail]:
        async with self.uow() as uow:
            return await uow.knowledge.search(
                query, limit, after=after, source_id=source_id, level=level
            )

    async def get(self, concept_id: UUID) -> ConceptDetail | None:
        async with self.uow() as uow:
            return await uow.knowledge.get(concept_id)

    async def provenance(self, concept_id: UUID) -> list[Provenance]:
        async with self.uow() as uow:
            return await uow.knowledge.provenance(concept_id)

    async def lineage(self, concept_id: UUID) -> LineageResult:
        async with self.uow() as uow:
            return LineageResult(
                concept_id=concept_id,
                origins=await uow.lineage.trace_origins(concept_id),
                dependents=await uow.lineage.find_dependents(concept_id),
            )

    async def related_requirements(self, concept_id: UUID) -> list[ConceptDetail]:
        async with self.uow() as uow:
            concept = await uow.knowledge.get(concept_id)
            if concept is None:
                return []
            ids = {
                edge.to_concept_id if edge.from_concept_id == concept_id else edge.from_concept_id
                for edge in concept.relationships
            }
            results = []
            for cid in ids:
                detail = await uow.knowledge.get(cid)
                if detail and detail.versions[-1].candidate.semantic_type in REQUIREMENT_TYPES:
                    results.append(detail)
            return results

    async def statistics(self) -> Statistics:
        async with self.uow() as uow:
            tasks: list[ProcessingTask] = await uow.processing.tasks(limit=1000)
            more = bool(
                len(tasks) == 1000 and await uow.processing.tasks(limit=1, after=tasks[-1].id)
            )
        usage = total_usage([task.usage for task in tasks])
        # Older accumulators could retain token counts but omit model calls and unknown prices.
        missing_call_counts = any(
            not task.usage.model_calls and (task.usage.input_tokens or task.usage.output_tokens)
            for task in tasks
        )
        return Statistics(
            task_count=len(tasks),
            tasks_truncated=more,
            failures=sum(t.status in {"failed", "cancelled"} for t in tasks),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model_calls=None if missing_call_counts else usage.model_calls,
            model_latency_ms=usage.latency_ms,
            configured_cost=None if missing_call_counts else usage.cost,
            validation_failures=usage.validation_failures,
        )
