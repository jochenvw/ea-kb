from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import text

from ekb.config import Settings
from ekb.domain.models import ConceptDetail, ProcessingTask, Provenance, ScanReport, SourceRecord
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

    app = FastAPI(title="Enterprise Knowledge Base", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
    templates = Jinja2Templates(directory=WEB / "templates")

    def active(request: Request) -> Runtime:
        return request.app.state.runtime

    @app.get("/health")
    async def health(request: Request) -> dict[str, str]:
        async with active(request).database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/api/sources", response_model=list[SourceRecord])
    async def sources(request: Request):
        async with active(request).database() as uow:
            return await uow.sources.list()

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

    @app.get("/api/concepts", response_model=list[ConceptDetail])
    async def concepts(
        request: Request,
        q: str = "",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ):
        return await active(request).knowledge.search(q, limit)

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
    async def index(request: Request, q: str = ""):
        context = {
            "sources": await sources(request),
            "scans": await scans(request),
            "tasks": await tasks(request),
            "concepts": await active(request).knowledge.search(q),
            "statistics": await statistics(request),
            "q": q,
            "scan_running": bool(
                request.app.state.scan_task and not request.app.state.scan_task.done()
            ),
        }
        name = "inventory.html" if request.headers.get("HX-Request") == "true" else "index.html"
        return templates.TemplateResponse(request=request, name=name, context=context)

    @app.get("/concepts/{concept_id}", response_class=HTMLResponse)
    async def concept_page(request: Request, concept_id: UUID):
        detail = await concept(request, concept_id)
        return templates.TemplateResponse(
            request=request,
            name="concept.html",
            context={
                "concept": detail,
                "provenance": await provenance(request, concept_id),
                "lineage": await lineage(request, concept_id),
            },
        )

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
