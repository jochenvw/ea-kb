from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from ekb.config import Settings
from ekb.domain.execution import StageOptions
from ekb.domain.issues import IssueStatus
from ekb.domain.models import Scope
from ekb.domain.resolution import Decision, ResolutionProfile
from ekb.interfaces.cli.presentation import ScanConsole, locations
from ekb.maintenance import reset_paths, reset_workspace
from ekb.runtime import Runtime


def register(app: typer.Typer, emit: Callable[[Any], None]) -> None:
    pipeline = typer.Typer(help="Durable, independently executable pipeline stages.")
    reconcile = typer.Typer(help="Evidence-backed proposals and append-only governance decisions.")
    issues = typer.Typer(help="Persistent, deduplicated issue registry.")
    knowledge = typer.Typer(help="Scope-aware knowledge resolution; canonical by default.")
    for name, commands in (
        ("pipeline", pipeline),
        ("reconcile", reconcile),
        ("issues", issues),
        ("knowledge", knowledge),
    ):
        app.add_typer(commands, name=name)

    def execute(operation) -> None:
        try:
            emit(asyncio.run(operation()))
        except (ValueError, ExceptionGroup) as error:
            raise typer.BadParameter(str(error)) from None

    @pipeline.command("run")
    def run(
        stages: Annotated[str | None, typer.Option()] = None,
        directory: Annotated[Path | None, typer.Option(exists=True, file_okay=False)] = None,
        manifest: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
        config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
        fresh: Annotated[
            bool, typer.Option(help="Erase EKB tables and owned local caches first.")
        ] = False,
        yes: Annotated[
            bool, typer.Option("--yes", "-y", help="Confirm --fresh without prompting.")
        ] = False,
        json_output: Annotated[
            bool, typer.Option("--json", help="Only emit the structured final report.")
        ] = False,
        output: Annotated[
            Path | None, typer.Option("--output", help="Persist a JSON run report here.")
        ] = None,
    ) -> None:
        """Acquire sources, extract knowledge, then reconcile and detect issues."""
        if fresh and stages:
            raise typer.BadParameter("--fresh requires the full pipeline; omit --stages")
        if fresh and json_output and not yes:
            raise typer.BadParameter(
                "--json --fresh requires --yes; no interactive prompt is emitted"
            )
        try:
            settings = Settings(config_file=config) if config else Settings()
        except ValidationError as error:
            messages = "; ".join(detail["msg"] for detail in error.errors(include_input=False))
            raise typer.BadParameter(messages) from None
        configuration = settings.pipeline
        options = configuration.stages.get("indexing", StageOptions())
        if fresh and not options.enabled:
            raise typer.BadParameter("--fresh requires enabled indexing")
        directory = directory or (
            Path(options.directory) if options.directory else settings.source_directory
        )
        manifest = manifest or (
            Path(options.manifest) if options.manifest else None if directory else settings.manifest
        )
        options = options.model_copy(
            update={
                "directory": str(directory.resolve()) if directory else None,
                "manifest": str(manifest.resolve()) if manifest else None,
            }
        )
        configuration = configuration.model_copy(
            update={"stages": {**configuration.stages, "indexing": options}}
        )
        console = ScanConsole(quiet=json_output)
        protected = tuple(path for path in (settings.config_file, manifest, directory) if path)
        try:
            console.start(settings, manifest, directory)
            if fresh:
                paths = reset_paths(settings, protected)
                console.line(
                    "FRESH RESET: permanently erase all EKB knowledge, decisions and caches."
                )
                console.line(f"  Database: {locations(settings, Path('report.json'))['database']}")
                for path in paths:
                    console.line(f"  Cache: {path}")
                console.line(
                    "  Migration state, unrelated tables, source inputs, reports "
                    "and logs are retained."
                )
                if not yes:
                    typer.confirm("Erase this EKB workspace and start fresh?", abort=True)
        except ValueError as error:
            raise typer.BadParameter(str(error)) from None
        except (SQLAlchemyError, OSError) as error:
            raise typer.BadParameter(
                f"Workspace unavailable ({type(error).__name__}); "
                "stop active scans and check database/cache access."
            ) from None

        async def operation():
            async with Runtime(settings) as runtime:
                if fresh:
                    await reset_workspace(settings, runtime.database, protected)
                    console.line("Reset complete.\n")
                async with runtime.database() as uow:
                    before = await uow.knowledge.counts()
                orchestrator = runtime.orchestrator(progress=console, staged=True)
                failure = None
                failure_detail = None
                try:
                    status = await orchestrator.run(
                        tuple(s.strip() for s in stages.split(",")) if stages else None,
                        configuration,
                    )
                except Exception as error:
                    failure = type(error).__name__
                    if (
                        isinstance(error, ValueError)
                        and str(error) == "Another pipeline owns the renewable execution lease"
                    ):
                        failure_detail = str(error)
                    status = await orchestrator.status(console.run_id) if console.run_id else None
                async with runtime.database() as uow:
                    after = await uow.knowledge.counts()
                report_path = (
                    output
                    or settings.data_dir / "reports" / f"pipeline-{console.run_id or 'failed'}.json"
                )
                links = locations(settings, report_path)
                result = status.model_dump(mode="json") if status else {}
                result.update(
                    {
                        "passed": failure is None and bool(status and status.run.status == "done"),
                        "error": failure,
                        "error_detail": failure_detail,
                        "fresh": fresh,
                        "scan": console.report.model_dump(mode="json") if console.report else None,
                        "before": before.model_dump(),
                        "after": after.model_dump(),
                        "links": links,
                    }
                )
                await asyncio.to_thread(report_path.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(
                    report_path.write_text, json.dumps(result, indent=2) + "\n", encoding="utf-8"
                )
                if status:
                    console.checks(status.executions)
                console.finish("done" if result["passed"] else "failed", before, after, links)
                if failure:
                    console.line(
                        f"Failure: {failure}. "
                        f"{failure_detail or 'Inspect the report and persisted pipeline status.'}"
                    )
                return result

        try:
            result = asyncio.run(operation())
        except ValueError as error:
            raise typer.BadParameter(str(error)) from None
        except (SQLAlchemyError, OSError) as error:
            raise typer.BadParameter(
                f"Workspace unavailable ({type(error).__name__}); "
                "stop active scans and check database/cache access."
            ) from None
        if json_output:
            emit(result)
        if not result["passed"]:
            raise typer.Exit(1)

    @pipeline.command("status")
    def status(run_id: Annotated[UUID | None, typer.Argument()] = None) -> None:
        async def operation():
            async with Runtime() as runtime:
                return await runtime.orchestrator().status(run_id)

        execute(operation)

    @pipeline.command("resume")
    def resume(run_id: UUID) -> None:
        async def operation():
            async with Runtime() as runtime:
                return await runtime.orchestrator().run(resume=run_id)

        execute(operation)

    @reconcile.command("list")
    def findings(
        limit: Annotated[int, typer.Option(min=1, max=1000)] = 100,
        check: Annotated[str | None, typer.Option()] = None,
        after: Annotated[UUID | None, typer.Option()] = None,
    ) -> None:
        async def operation():
            async with Runtime() as runtime:
                return await runtime.reconciliation.findings(limit, check_id=check, after=after)

        execute(operation)

    @reconcile.command("history")
    def history(finding_id: Annotated[UUID | None, typer.Argument()] = None) -> None:
        async def operation():
            async with Runtime() as runtime:
                return await runtime.reconciliation.history(finding_id)

        execute(operation)

    @reconcile.command("decide")
    def decide(
        finding_id: UUID,
        outcome: str,
        actor: Annotated[str, typer.Option()],
        rationale: Annotated[str, typer.Option()],
        previous: Annotated[UUID | None, typer.Option()] = None,
        decision_id: Annotated[UUID | None, typer.Option()] = None,
        effective_at: Annotated[str | None, typer.Option()] = None,
    ) -> None:
        async def operation():
            data: dict[str, Any] = dict(
                finding_id=finding_id,
                outcome=outcome,
                actor=actor,
                rationale=rationale,
                previous_id=previous,
            )
            if decision_id:
                data["id"] = decision_id
            if effective_at:
                data["effective_at"] = date.fromisoformat(effective_at)
            decision = Decision.model_validate(data)
            async with Runtime() as runtime:
                return await runtime.reconciliation.decide(decision)

        execute(operation)

    @reconcile.command("canonical")
    def canonical(
        concept_id: UUID,
        version_id: UUID,
        scope: Annotated[str, typer.Option(help="JSON applicability scope.")] = "{}",
        allow_prerelease: Annotated[bool, typer.Option()] = False,
    ) -> None:
        """Propose, never silently accept, a scoped canonical selection."""

        async def operation():
            async with Runtime() as runtime:
                return await runtime.reconciliation.propose_canonical(
                    concept_id, version_id, Scope.model_validate_json(scope), allow_prerelease
                )

        execute(operation)

    @issues.command("list")
    def issue_list(
        status: Annotated[str | None, typer.Option()] = None,
        issue_type: Annotated[str | None, typer.Option("--type")] = None,
        severity: Annotated[str | None, typer.Option()] = None,
        scope: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    ) -> None:
        async def operation():
            async with Runtime() as runtime:
                return await runtime.issues.list(
                    TypeAdapter(IssueStatus).validate_python(status) if status else None,
                    issue_type,
                    severity,
                    limit,
                    Scope.model_validate_json(scope) if scope else None,
                )

        execute(operation)

    @issues.command("show")
    def issue_show(issue_id: UUID) -> None:
        async def operation():
            async with Runtime() as runtime:
                return await runtime.issues.get(issue_id)

        execute(operation)

    @issues.command("review")
    def review(
        issue_id: UUID,
        status: str,
        actor: Annotated[str, typer.Option()],
        rationale: Annotated[str, typer.Option()],
        owner: Annotated[str | None, typer.Option()] = None,
    ) -> None:
        async def operation():
            async with Runtime() as runtime:
                return await runtime.issues.review(
                    issue_id,
                    TypeAdapter(IssueStatus).validate_python(status),
                    actor,
                    rationale,
                    owner,
                )

        execute(operation)

    @knowledge.command("resolve")
    def resolve(
        concept_id: UUID,
        profile: Annotated[str, typer.Option()] = "canonical",
        scope: Annotated[str, typer.Option()] = "{}",
        as_of: Annotated[str | None, typer.Option()] = None,
    ) -> None:
        async def operation():
            async with Runtime() as runtime:
                return await runtime.resolver.resolve_concept(
                    concept_id,
                    Scope.model_validate_json(scope),
                    TypeAdapter(ResolutionProfile).validate_python(profile),
                    date.fromisoformat(as_of) if as_of else None,
                )

        execute(operation)

    @knowledge.command("search")
    def search(
        query: str,
        profile: Annotated[str, typer.Option()] = "canonical",
        scope: Annotated[str, typer.Option()] = "{}",
    ) -> None:
        async def operation():
            async with Runtime() as runtime:
                return await runtime.resolver.resolve_requirement(
                    query,
                    Scope.model_validate_json(scope),
                    TypeAdapter(ResolutionProfile).validate_python(profile),
                )

        execute(operation)
