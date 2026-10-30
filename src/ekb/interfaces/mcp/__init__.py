from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from ekb.domain.issues import Issue, IssueStatus
from ekb.domain.models import ConceptDetail, Provenance, Scope
from ekb.domain.resolution import Decision, Resolution, ResolutionProfile
from ekb.runtime import Runtime
from ekb.services.issue_service import IssueService
from ekb.services.knowledge import KnowledgeService
from ekb.services.knowledge_resolver import KnowledgeResolver
from ekb.services.reconciliation_service import ReconciliationService


def create_server(service: KnowledgeService | None = None) -> FastMCP:
    @asynccontextmanager
    async def lifespan(server: FastMCP):
        if service:
            yield {
                "knowledge": service,
                "resolver": KnowledgeResolver(service.uow),
                "issues": IssueService(service.uow),
                "reconciliation": ReconciliationService(service.uow),
            }
        else:
            async with Runtime() as runtime:
                yield {
                    "knowledge": runtime.knowledge,
                    "resolver": runtime.resolver,
                    "issues": runtime.issues,
                    "reconciliation": runtime.reconciliation,
                }

    server = FastMCP(
        "Enterprise Knowledge Base",
        instructions="Use resolve_knowledge or resolve_requirement for authoritative answers; "
        "their default is strict canonical with no candidate fallback. Search and inspection "
        "are research views, not approved compliance conclusions.",
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

    @server.tool()
    async def resolve_knowledge(
        concept_id: UUID,
        ctx: Context,
        scope: Scope | None = None,
        profile: ResolutionProfile = "canonical",
        as_of: date | None = None,
    ) -> Resolution:
        """Resolve accepted identities and scoped authority; no silent draft/candidate fallback."""
        resolver: KnowledgeResolver = ctx.request_context.lifespan_context["resolver"]
        return await resolver.resolve_concept(concept_id, scope, profile, as_of)

    @server.tool()
    async def resolve_requirement(
        query: str,
        ctx: Context,
        scope: Scope | None = None,
        profile: ResolutionProfile = "canonical",
    ) -> tuple[Resolution, ...]:
        """Literal-text requirement search with deduplication and strict canonical defaults."""
        resolver: KnowledgeResolver = ctx.request_context.lifespan_context["resolver"]
        return await resolver.resolve_requirement(query, scope, profile)

    @server.tool()
    async def list_knowledge_issues(
        ctx: Context,
        status: IssueStatus | None = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 100,
    ) -> list[Issue]:
        """Inspect the prioritized issue registry; detection does not grant authority."""
        issues: IssueService = ctx.request_context.lifespan_context["issues"]
        return await issues.list(status=status, limit=limit)

    @server.tool()
    async def get_reconciliation_history(
        ctx: Context,
        finding_id: UUID | None = None,
    ) -> list[Decision]:
        """Read immutable accepted/rejected/deferred/reversed governance decisions."""
        reconciliation: ReconciliationService = ctx.request_context.lifespan_context[
            "reconciliation"
        ]
        return await reconciliation.history(finding_id)

    return server
