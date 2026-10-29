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

from ekb import __version__
from ekb.config import Settings
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


def emit(value: Any) -> None:
    def encode(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        return str(item)

    typer.echo(json.dumps(value, default=encode, indent=2))


@app.command()
def init() -> None:
    """Migrate PostgreSQL; never drops existing knowledge."""
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not Path("alembic.ini").is_file():
        raise typer.BadParameter("Run init from the project checkout containing alembic.ini")
    command.upgrade(Config("alembic.ini"), "head")
    emit({"status": "initialized", "version": __version__})


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


@sources_app.command("sync")
def sources_sync(
    provider: str,
    versions: Annotated[str | None, typer.Option(help="Comma-separated edition keys.")] = None,
    domains: Annotated[
        str | None, typer.Option(help="Comma-separated domain IDs, e.g. NS.")
    ] = None,
) -> None:
    """Download and normalize official benchmark content; preserve originals and resumable state."""
    if provider != "microsoft-security-benchmark":
        raise typer.BadParameter("Supported provider: microsoft-security-benchmark")

    async def execute():
        from ekb.observability import tracer

        async with Runtime() as runtime:
            with tracer.start_as_current_span("benchmark.sync"):
                return await runtime.benchmarks().sync(
                    [value.strip() for value in versions.split(",")] if versions else None,
                    [value.strip() for value in domains.split(",")] if domains else None,
                )

    try:
        report = asyncio.run(execute())
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    emit(report)
    if report.failed:
        raise typer.Exit(1)


@scans_app.command("list")
def scans_list() -> None:
    async def execute():
        async with Runtime() as runtime, runtime.database() as uow:
            return await uow.processing.scans()

    emit(asyncio.run(execute()))


@concepts_app.command("list")
def concepts_list(
    query: str = "", limit: Annotated[int, typer.Option(min=1, max=200)] = 50
) -> None:
    async def execute():
        async with Runtime() as runtime:
            return await runtime.knowledge.search(query, limit)

    emit(asyncio.run(execute()))


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


@app.command()
def lineage(concept_id: UUID) -> None:
    async def execute():
        async with Runtime() as runtime:
            return await runtime.knowledge.lineage(concept_id)

    emit(asyncio.run(execute()))


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
def tui(
    directory: Annotated[Path | None, typer.Option()] = None,
    manifest: Annotated[Path | None, typer.Option()] = None,
) -> None:
    from ekb.interfaces.tui import KnowledgeApp

    async def execute():
        async with Runtime() as runtime:
            await KnowledgeApp(runtime, directory, manifest).run_async()

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
