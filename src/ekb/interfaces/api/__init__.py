from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import text
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from ekb import __version__
from ekb.adapters.ontology import load_ontology
from ekb.config import Settings
from ekb.domain.models import (
    ConceptDetail,
    KnowledgeLevel,
    ProcessingTask,
    Provenance,
    ScanReport,
    SourceRecord,
)
from ekb.domain.ontology import OntologyCatalog
from ekb.domain.reconciliation import (
    BenchmarkLineage,
    ComparisonReport,
    ControlContext,
    EditionView,
)
from ekb.domain.sources import SourceGroup, source_groups
from ekb.interfaces.api.governance import register as register_governance
from ekb.interfaces.api.inspection import register as register_inspection
from ekb.observability import log
from ekb.runtime import Runtime
from ekb.services.knowledge import LineageResult, Statistics

WEB = Path(__file__).parent.parent / "web"


def create_app(runtime: Runtime | None = None) -> FastAPI:
    provided_runtime = runtime
    settings = runtime.settings if runtime else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active = provided_runtime or Runtime(settings)
        app.state.runtime = active
        app.state.scan_task = None
        try:
            yield
        finally:
            task = cast(asyncio.Task[None] | None, app.state.scan_task)
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if provided_runtime is None:
                await active.__aexit__()

    app = FastAPI(title="Enterprise Knowledge Base", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
    templates = Jinja2Templates(directory=WEB / "static" / "ui")

    @app.middleware("http")
    async def require_loopback(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path != "/health":
            try:
                loopback = (
                    request.client is not None and ip_address(request.client.host).is_loopback
                )
            except ValueError:
                loopback = False
            forwarded = any(
                name == "forwarded" or name == "x-real-ip" or name.startswith("x-forwarded-")
                for name in request.headers
            )
            if not loopback or forwarded:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Remote access is disabled; use a loopback connection."},
                )
        return await call_next(request)

    def active(request: Request) -> Runtime:
        return request.app.state.runtime

    register_governance(app, active, templates)
    register_inspection(app, active)

    @app.get("/api/ontology", response_model=OntologyCatalog)
    async def ontology() -> OntologyCatalog:
        return await asyncio.to_thread(load_ontology)

    @app.get("/health")
    async def health(request: Request) -> dict[str, str]:
        async with active(request).database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/api/sources", response_model=list[SourceRecord])
    async def sources(request: Request):
        async with active(request).database() as uow:
            return await uow.sources.list()

    @app.get("/api/source-groups", response_model=list[SourceGroup])
    async def logical_sources(request: Request):
        async with active(request).database() as uow:
            return source_groups(await uow.sources.list())

    @app.get("/api/scans", response_model=list[ScanReport])
    async def scans(request: Request):
        async with active(request).database() as uow:
            return await uow.processing.scans()

    @app.get("/api/tasks", response_model=list[ProcessingTask])
    async def tasks(request: Request):
        async with active(request).database() as uow:
            return await uow.processing.tasks()

    @app.get("/api/statistics", response_model=Statistics)
    async def statistics(request: Request):
        return await active(request).knowledge.statistics()

    @app.get("/api/ui/config")
    async def ui_configuration(request: Request) -> dict[str, bool]:
        task = request.app.state.scan_task
        return {
            "governance_enabled": bool(
                active(request).settings.governance_token.get_secret_value()
            ),
            "scan_running": bool(task and not task.done()),
        }

    @app.get("/api/benchmarks/compare", response_model=ComparisonReport)
    async def compare_benchmarks(
        request: Request,
        from_edition: str = "mcsb-v1",
        to_edition: str = "mcsb-v2",
        control_id: str | None = None,
    ):
        try:
            return await active(request).comparisons.compare(
                from_edition, to_edition, control_id, read_only=True
            )
        except ValueError as error:
            raise HTTPException(404, str(error)) from None

    @app.get("/api/benchmarks/editions/{edition}", response_model=EditionView)
    async def benchmark_edition(request: Request, edition: str):
        try:
            return await active(request).comparisons.edition(edition)
        except ValueError as error:
            raise HTTPException(404, str(error)) from None

    @app.get("/api/benchmarks/controls/{reference}", response_model=ControlContext)
    async def benchmark_control(request: Request, reference: str):
        try:
            return await active(request).comparisons.control(reference)
        except ValueError as error:
            raise HTTPException(404, str(error)) from None

    @app.get("/api/benchmarks/controls/{reference}/lineage", response_model=BenchmarkLineage)
    async def benchmark_lineage(request: Request, reference: str):
        try:
            return await active(request).comparisons.lineage(reference)
        except ValueError as error:
            raise HTTPException(404, str(error)) from None

    @app.get("/api/concepts", response_model=list[ConceptDetail])
    async def concepts(
        request: Request,
        q: str = "",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        after: UUID | None = None,
        source_id: UUID | None = None,
        level: KnowledgeLevel | None = None,
    ):
        return await active(request).knowledge.search(
            q, limit, after=after, source_id=source_id, level=level
        )

    @app.get("/api/concepts/{concept_id}", response_model=ConceptDetail)
    async def concept(request: Request, concept_id: UUID):
        result = await active(request).knowledge.get(concept_id)
        if result is None:
            raise HTTPException(404, "Concept not found")
        return result

    @app.get("/api/concepts/{concept_id}/provenance", response_model=list[Provenance])
    async def provenance(request: Request, concept_id: UUID):
        return await active(request).knowledge.provenance(concept_id)

    @app.get("/api/concepts/{concept_id}/lineage", response_model=LineageResult)
    async def lineage(request: Request, concept_id: UUID):
        return await active(request).knowledge.lineage(concept_id)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/knowledge", response_class=HTMLResponse)
    @app.get("/sources", response_class=HTMLResponse)
    @app.get("/pipeline", response_class=HTMLResponse)
    @app.get("/benchmarks", response_class=HTMLResponse)
    @app.get("/explore", response_class=HTMLResponse)
    @app.get("/reconciliation", response_class=HTMLResponse)
    @app.get("/data", response_class=HTMLResponse)
    @app.get("/logs", response_class=HTMLResponse)
    @app.get("/ontology", response_class=HTMLResponse)
    async def workspace(request: Request):
        return templates.TemplateResponse(request=request, name="index.html")

    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    async def source_page(request: Request, source_id: UUID):
        async with active(request).database() as uow:
            if await uow.sources.get(source_id) is None:
                if await uow.inspection.source(source_id, 1) is None:
                    raise HTTPException(404, "Source not found")
        return templates.TemplateResponse(request=request, name="index.html")

    @app.get("/concepts/{concept_id}", response_class=HTMLResponse)
    async def concept_page(request: Request, concept_id: UUID):
        await concept(request, concept_id)
        return templates.TemplateResponse(request=request, name="index.html")

    @app.post("/scans")
    async def start_scan(
        request: Request,
        public_manifest: Annotated[bool, Form()] = False,
    ):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin scan requests are not allowed")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site scan requests are not allowed")
        running = request.app.state.scan_task
        if running and not running.done():
            raise HTTPException(409, "A scan is already running")
        runtime = active(request)
        try:
            connectors = runtime.connectors(
                manifest=runtime.settings.manifest if public_manifest else None
            )
        except ValueError:
            raise HTTPException(
                400, "Configure EKB_SOURCE_DIRECTORY or select public sources"
            ) from None

        async def run_scan() -> None:
            try:
                await runtime.pipeline().scan(connectors)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.error("web_scan_failed", error_type=type(error).__name__)

        request.app.state.scan_task = asyncio.create_task(run_scan())
        return RedirectResponse("/", status_code=303)

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
    return app
