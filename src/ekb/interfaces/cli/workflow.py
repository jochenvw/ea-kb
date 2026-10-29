from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer
from pydantic import TypeAdapter

from ekb.domain.execution import StageOptions
from ekb.domain.issues import IssueStatus
from ekb.domain.models import Scope
from ekb.domain.resolution import Decision, ResolutionProfile
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
        directory: Annotated[Path | None, typer.Option()] = None,
    ) -> None:
        async def operation():
            async with Runtime() as runtime:
                configuration = runtime.settings.pipeline
                if directory:
                    options = configuration.stages.get("indexing", StageOptions())
                    configuration = configuration.model_copy(
                        update={
                            "stages": {
                                **configuration.stages,
                                "indexing": options.model_copy(
                                    update={"directory": str(directory.resolve())}
                                ),
                            },
                        }
                    )
                return await runtime.orchestrator().run(
                    tuple(s.strip() for s in stages.split(",")) if stages else None,
                    configuration,
                )

        execute(operation)

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
