from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from ekb.domain.models import ConceptDetail, Provenance
from ekb.runtime import Runtime
from ekb.services.knowledge import KnowledgeService


def create_server(service: KnowledgeService | None = None) -> FastMCP:
    @asynccontextmanager
    async def lifespan(server: FastMCP):
        if service:
            yield {"knowledge": service}
        else:
            async with Runtime() as runtime:
                yield {"knowledge": runtime.knowledge}

    server = FastMCP(
        "Enterprise Knowledge Base",
        instructions="Read-only evidence-grounded candidates. Not approved compliance conclusions.",
        lifespan=lifespan,
    )

    def knowledge(context: Context) -> KnowledgeService:
        return service or context.request_context.lifespan_context["knowledge"]

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

    return server
