import asyncio
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request

from ekb.domain.inspection import (
    BacklogPage,
    BacklogStatus,
    KnowledgeGraph,
    LogPage,
    SourceInspection,
    TableInfo,
    TablePage,
)
from ekb.observability.inspection import read_events
from ekb.runtime import Runtime


def register(app: FastAPI, active: Callable[[Request], Runtime]) -> None:
    @app.get("/api/sources/{source_id}", response_model=SourceInspection)
    async def source(
        request: Request,
        source_id: UUID,
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
        after: UUID | None = None,
    ):
        async with active(request).database() as uow:
            result = await uow.inspection.source(source_id, limit, after=after)
        if result is None:
            raise HTTPException(404, "Source not found")
        return result

    @app.get("/api/concepts/{concept_id}/graph", response_model=KnowledgeGraph)
    async def graph(request: Request, concept_id: UUID):
        async with active(request).database() as uow:
            result = await uow.inspection.graph(concept_id)
        if result is None:
            raise HTTPException(404, "Concept not found")
        return result

    @app.get("/api/reconciliation/backlog", response_model=BacklogPage)
    async def backlog(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
        after: UUID | None = None,
        status: BacklogStatus = "all",
        check_id: Annotated[str | None, Query(max_length=100)] = None,
    ):
        async with active(request).database() as uow:
            return await uow.inspection.backlog(
                limit, after=after, status=status, check_id=check_id
            )

    @app.get("/api/inspection/tables", response_model=list[TableInfo])
    async def tables(request: Request):
        async with active(request).database() as uow:
            return await uow.inspection.tables()

    @app.get("/api/inspection/tables/{name}", response_model=TablePage)
    async def table(
        request: Request,
        name: str,
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
        after: Annotated[str | None, Query(max_length=200)] = None,
        record: Annotated[str | None, Query(max_length=200)] = None,
    ):
        try:
            async with active(request).database() as uow:
                return await uow.inspection.rows(name, limit, after=after, record=record)
        except ValueError as error:
            raise HTTPException(404, str(error)) from None

    @app.get("/api/inspection/logs", response_model=LogPage)
    async def logs(request: Request, limit: Annotated[int, Query(ge=1, le=200)] = 100):
        try:
            return await asyncio.to_thread(read_events, active(request).settings.data_dir, limit)
        except OSError:
            raise HTTPException(503, "Operational logs are temporarily unavailable") from None
