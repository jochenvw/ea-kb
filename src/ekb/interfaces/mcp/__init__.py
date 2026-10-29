from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from ekb.domain.models import ConceptDetail, Provenance
from ekb.domain.reconciliation import (
    BenchmarkLineage,
    ComparisonReport,
    ControlContext,
    EditionView,
)
from ekb.runtime import Runtime
from ekb.services.knowledge import KnowledgeService
from ekb.services.reconciliation import BenchmarkService


def create_server(
    service: KnowledgeService | None = None, benchmarks: BenchmarkService | None = None
) -> FastMCP:
    @asynccontextmanager
    async def lifespan(server: FastMCP):
        if service:
            yield {"knowledge": service, "benchmarks": benchmarks or BenchmarkService(service.uow)}
        else:
            async with Runtime() as runtime:
                yield {"knowledge": runtime.knowledge, "benchmarks": runtime.comparisons}

    server = FastMCP(
        "Enterprise Knowledge Base",
        instructions="Read-only evidence-grounded candidates. Not approved compliance conclusions.",
        lifespan=lifespan,
    )

    def knowledge(context: Context) -> KnowledgeService:
        return service or context.request_context.lifespan_context["knowledge"]

    def benchmark(context: Context) -> BenchmarkService:
        return benchmarks or context.request_context.lifespan_context["benchmarks"]

    @server.tool()
    async def search_concepts(
        query: str, ctx: Context, limit: Annotated[int, Field(ge=1, le=200)] = 50
    ) -> list[ConceptDetail]:
        """Search candidate statements; textual modality does not confer authority."""
        return await knowledge(ctx).search(query, limit)

    @server.tool()
    async def get_concept(concept_id: UUID, ctx: Context) -> ConceptDetail | None:
        """Get immutable revisions, occurrences, relationships and authority history."""
        return await knowledge(ctx).get(concept_id)

    @server.tool()
    async def trace_provenance(concept_id: UUID, ctx: Context) -> list[Provenance]:
        """Resolve exact excerpts to source segments, revisions, artifacts and locations."""
        return await knowledge(ctx).provenance(concept_id)

    @server.tool()
    async def find_related_requirements(concept_id: UUID, ctx: Context) -> list[ConceptDetail]:
        """Return requirements connected by recorded evidence-backed relationships."""
        return await knowledge(ctx).related_requirements(concept_id)

    @server.tool()
    async def get_benchmark_control(reference: str, ctx: Context) -> ControlContext:
        """Inspect edition-qualified controls, native mappings, policy references and authority."""
        return await benchmark(ctx).control(reference)

    @server.tool()
    async def get_benchmark_edition(edition: str, ctx: Context) -> EditionView:
        """Get independent publication maturity and exact published succession evidence."""
        return await benchmark(ctx).edition(edition)

    @server.tool()
    async def compare_benchmark_editions(
        from_edition: str, to_edition: str, ctx: Context, control_id: str | None = None
    ) -> ComparisonReport:
        """Read a saved comparison or structural preview; never call models or persist proposals."""
        return await benchmark(ctx).compare(from_edition, to_edition, control_id, read_only=True)

    @server.tool()
    async def trace_benchmark_lineage(reference: str, ctx: Context) -> BenchmarkLineage:
        """Trace published mappings/succession separately from review-only semantic proposals."""
        return await benchmark(ctx).lineage(reference)

    return server
