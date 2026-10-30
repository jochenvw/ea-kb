from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer
from alembic import command
from alembic.config import Config
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ekb import __version__
from ekb.config import Settings
from ekb.domain.models import KnowledgeLevel
from ekb.interfaces.cli.evaluation import register as register_evaluation
from ekb.interfaces.cli.workflow import register
from ekb.maintenance import reset_workspace
from ekb.runtime import Runtime

app = typer.Typer(help="Enterprise Knowledge Base: incremental, grounded knowledge.")
sources_app = typer.Typer()
scans_app = typer.Typer()
concepts_app = typer.Typer()
eval_app = typer.Typer()
app.add_typer(sources_app, name="sources")
app.add_typer(scans_app, name="scans")
app.add_typer(concepts_app, name="concepts")
app.add_typer(eval_app, name="eval")


@eval_app.command("workflow")
def evaluate_workflow(
    fixture: Annotated[Path, typer.Option()] = Path("tests/fixtures/enterprise.json"),
    output: Annotated[Path, typer.Option()] = Path("data/phase3-demonstration.json"),
) -> None:
    """Run retained real sources and simulated governance in the persistent demo schema."""
    from ekb.evaluation.workflow import demonstrate_workflow

    emit(asyncio.run(demonstrate_workflow(Settings(), fixture, output)))


def emit(value: Any) -> None:
    def encode(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        return str(item)

    typer.echo(json.dumps(value, default=encode, indent=2))


register(app, emit)
register_evaluation(app, eval_app, emit)


@app.command()
def reset(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Confirm permanent reset.")] = False,
) -> None:
    """Clear EKB-owned database state without deleting originals or starting ingestion."""
    settings = Settings()
    if not yes:
        typer.confirm(
            "Permanently erase EKB knowledge, runs and decisions? Originals are retained.",
            abort=True,
        )

    async def execute() -> None:
        async with Runtime(settings) as runtime:
            await reset_workspace(runtime.database)

    try:
        asyncio.run(execute())
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    except (SQLAlchemyError, OSError) as error:
        raise typer.BadParameter(
            f"Reset did not finish ({type(error).__name__}); check database access."
        ) from None
    emit({"status": "reset", "originals": "retained"})


@app.command()
def init(
    recreate: Annotated[
        bool,
        typer.Option(help="Drop/recreate EKB-owned tables; allowed before v1 only."),
    ] = False,
) -> None:
    """Initialize the current development schema; use migrations from v1 onward."""
    development = int(__version__.partition(".")[0]) < 1
    if recreate and not development:
        raise typer.BadParameter("--recreate is only available before application v1")
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if development:

        async def execute() -> None:
            async with Runtime(settings) as runtime:
                if recreate:
                    await reset_workspace(runtime.database, recreate=True)
                else:
                    await runtime.database.initialize()

        asyncio.run(execute())
    else:
        if not Path("alembic.ini").is_file():
            raise typer.BadParameter("Run init from the project checkout containing alembic.ini")
        command.upgrade(Config("alembic.ini"), "head")
    emit({"status": "initialized", "version": __version__, "recreated": recreate})


@app.command()
def scan(
    directory: Annotated[Path | None, typer.Argument()] = None,
    manifest: Annotated[Path | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
) -> None:
    """Start or resume ingestion. Dry-run performs no downloads, writes, or inference."""

    async def execute():
        async with Runtime() as runtime:
            return await runtime.pipeline().scan(
                runtime.connectors(directory, manifest), dry_run=dry_run
            )

    try:
        report = asyncio.run(execute())
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    emit(report)
    if report.failed:
        raise typer.Exit(1)


@sources_app.command("list")
def sources_list() -> None:
    async def execute():
        async with Runtime() as runtime, runtime.database() as uow:
            return await uow.sources.list()

    emit(asyncio.run(execute()))


@scans_app.command("list")
def scans_list() -> None:
    """Inspect recent persisted scan reports, including source failure identifiers."""

    async def execute():
        async with Runtime() as runtime, runtime.database() as uow:
            return await uow.processing.scans()

    emit(asyncio.run(execute()))


@scans_app.command("tasks")
def scans_tasks(
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    after: Annotated[UUID | None, typer.Option(help="Continue after this task ID.")] = None,
) -> None:
    """Inspect processing failures, attempts and per-task usage in bounded pages."""

    async def execute():
        async with Runtime() as runtime, runtime.database() as uow:
            return await uow.processing.tasks(limit=limit, after=after)

    emit(asyncio.run(execute()))


@scans_app.command("usage")
def scans_usage() -> None:
    """Summarize usage of up to 1,000 persisted tasks; unpriced cost is unknown."""

    async def execute():
        async with Runtime() as runtime:
            return await runtime.knowledge.statistics()

    emit(asyncio.run(execute()))


@concepts_app.command("list")
def concepts_list(
    query: str = "",
    limit: Annotated[int, typer.Option(min=1, max=200)] = 50,
    source_id: Annotated[UUID | None, typer.Option()] = None,
    level: Annotated[KnowledgeLevel | None, typer.Option()] = None,
) -> None:
    async def execute():
        async with Runtime() as runtime:
            return await runtime.knowledge.search(query, limit, source_id=source_id, level=level)

    emit(asyncio.run(execute()))


@concepts_app.command("phrase")
def concepts_phrase(
    source_id: Annotated[
        UUID | None, typer.Option(help="Restrict to an exact source document.")
    ] = None,
    concurrency: Annotated[int, typer.Option(min=1, max=16)] = 4,
) -> None:
    """Cache source-grounded one-line assertions using the configured model; retain originals."""

    async def execute():
        async with Runtime() as runtime:
            return await runtime.assertions().generate(source_id=source_id, concurrency=concurrency)

    try:
        report = asyncio.run(execute())
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    emit(report)
    if report.failed:
        raise typer.Exit(1)


@concepts_app.command("show")
def concepts_show(concept_id: UUID) -> None:
    async def execute():
        async with Runtime() as runtime:
            concept = await runtime.knowledge.get(concept_id)
            if concept is None:
                raise typer.Exit(1)
            return {
                "concept": concept,
                "provenance": await runtime.knowledge.provenance(concept_id),
            }

    emit(asyncio.run(execute()))


@concepts_app.command("search")
def concepts_search(query: str, limit: Annotated[int, typer.Option(min=1, max=200)] = 50) -> None:
    """Search goal and requirement statements."""
    concepts_list(query, limit)


@app.command()
def lineage(concept_id: UUID) -> None:
    async def execute():
        async with Runtime() as runtime:
            return await runtime.knowledge.lineage(concept_id)

    try:
        emit(asyncio.run(execute()))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None


@app.command()
def doctor() -> None:
    """Check configuration, migrations, database and optional infrastructure."""

    async def execute():
        checks: dict[str, Any] = {"python": "3.13", "version": __version__}
        try:
            async with Runtime() as runtime:
                async with runtime.database.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                    revision = await connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                checks.update(
                    {
                        "database": "ok",
                        "migration": revision,
                        "inference": runtime.inference.capabilities.model_dump(),
                        "artifacts": runtime.settings.artifact_backend,
                        "ocr": runtime.settings.ocr,
                        "telemetry": str(runtime.settings.data_dir / "telemetry"),
                    }
                )
        except Exception as error:
            checks["error"] = type(error).__name__
        return checks

    checks = asyncio.run(execute())
    emit(checks)
    if "error" in checks:
        raise typer.Exit(1)


@eval_app.command("run")
def eval_run(
    dataset: Path = Path("tests/golden/dataset.json"),
    output: Path = Path("data/evaluation.json"),
    baseline: Annotated[bool, typer.Option()] = False,
) -> None:
    """Evaluate offline rules or the configured model; never fabricates model results."""
    from ekb.evaluation import evaluate

    async def execute():
        async with Runtime() as runtime:
            from ekb.adapters.inference import RulesInference

            inference = RulesInference() if baseline else runtime.inference
            return await evaluate(dataset, output, inference)

    report = asyncio.run(execute())
    emit(
        {
            "provider": report["provider"],
            "metrics": report["metrics"],
            "documents": len(report["documents"]),
            "report": str(output),
            "summary": str(output.with_suffix(".txt")),
        }
    )


@app.command()
def tui() -> None:
    from ekb.interfaces.tui import KnowledgeApp

    async def execute():
        async with Runtime() as runtime:
            await KnowledgeApp(runtime).run_async()

    asyncio.run(execute())


@app.command()
def web(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run("ekb.interfaces.api:create_app", factory=True, host=host, port=port)


@app.command()
def mcp() -> None:
    from ekb.interfaces.mcp import create_server

    create_server().run(transport="stdio")


@app.command()
def worker(
    directory: Annotated[Path | None, typer.Option()] = None,
    manifest: Annotated[Path | None, typer.Option()] = None,
    once: Annotated[bool, typer.Option()] = False,
) -> None:
    async def execute():
        async with Runtime() as runtime:
            connectors = runtime.connectors(directory, manifest)
            while True:
                report = await runtime.pipeline().scan(connectors)
                if once:
                    if report.failed:
                        raise typer.Exit(1)
                    return
                await asyncio.sleep(runtime.settings.worker_interval)

    try:
        asyncio.run(execute())
    except KeyboardInterrupt:
        pass
