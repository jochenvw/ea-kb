from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import typer
import yaml
from pydantic import TypeAdapter, ValidationError

from ekb.adapters.implementation import compile_bicep
from ekb.domain.architectures import ArchitectureEvidence, ArchitectureManifest
from ekb.domain.assessment import SolutionAssessment, SolutionAssessmentInput
from ekb.domain.portfolio import ChangeImpactInput, ExceptionRiskInput
from ekb.runtime import Runtime


def register(app: typer.Typer, emit: Callable[[Any], None]) -> None:
    architectures = typer.Typer(help="Register and inspect solution architecture artefacts.")
    app.add_typer(architectures, name="architectures")

    @architectures.command("register")
    def register_architecture(
        manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
        artifact: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
        media_type: Annotated[str | None, typer.Option()] = None,
    ) -> None:
        """Register one immutable architecture revision from YAML plus its artefact."""
        try:
            specification = ArchitectureManifest.model_validate(
                yaml.safe_load(manifest.read_text(encoding="utf-8"))
            )
            content = artifact.read_bytes()
            selected_media_type = (
                media_type or mimetypes.guess_type(artifact.name)[0] or "application/octet-stream"
            )

            async def execute():
                async with Runtime() as runtime:
                    return await runtime.architectures.register(
                        specification,
                        artifact.name,
                        selected_media_type,
                        content,
                    )

            emit(asyncio.run(execute()))
        except (OSError, ValueError, ValidationError, yaml.YAMLError) as error:
            raise typer.BadParameter(str(error)) from None

    @architectures.command("list")
    def list_architectures(
        limit: Annotated[int, typer.Option(min=1, max=200)] = 50,
        after: Annotated[UUID | None, typer.Option()] = None,
    ) -> None:
        async def execute():
            async with Runtime() as runtime:
                return await runtime.architectures.list(limit, after=after)

        emit(asyncio.run(execute()))

    @architectures.command("show")
    def show_architecture(architecture_id: UUID) -> None:
        async def execute():
            async with Runtime() as runtime:
                result = await runtime.architectures.get(architecture_id)
                if result is None:
                    raise ValueError("Solution architecture not found")
                return result

        try:
            emit(asyncio.run(execute()))
        except ValueError as error:
            raise typer.BadParameter(str(error)) from None

    @architectures.command("assess")
    def assess_architecture(
        architecture_id: UUID,
        evidence: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
        version: Annotated[str | None, typer.Option()] = None,
        as_of: Annotated[str | None, typer.Option(help="ISO date, for example 2026-11-01.")] = None,
    ) -> None:
        """Assess declared implementation evidence; this does not inspect source code."""
        try:
            claims = TypeAdapter(tuple[ArchitectureEvidence, ...]).validate_python(
                json.loads(evidence.read_text(encoding="utf-8"))
            )

            async def execute():
                async with Runtime() as runtime:
                    return await runtime.architectures.assess(
                        architecture_id,
                        claims,
                        version=version,
                        as_of=date.fromisoformat(as_of) if as_of else None,
                    )

            emit(asyncio.run(execute()))
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
            raise typer.BadParameter(str(error)) from None

    @app.command("assess")
    def assess_solution(
        architecture_id: UUID,
        template: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
        baseline_concept: Annotated[UUID, typer.Option(help="Approved baseline concept ID.")],
        baseline_version: Annotated[UUID, typer.Option(help="Approved baseline version ID.")],
        parameters: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
        version: Annotated[str | None, typer.Option()] = None,
        as_of: Annotated[str | None, typer.Option(help="ISO date, for example 2026-11-01.")] = None,
        commit: Annotated[str | None, typer.Option()] = None,
        require: Annotated[
            str, typer.Option(help="conforms, acceptable (approved exceptions) or report.")
        ] = "conforms",
    ) -> None:
        """Assess an ARM/Bicep template and design against an approved baseline (CI-friendly).

        Exits 1 when the required result is not established; unknown never counts as passing.
        """
        if require not in {"conforms", "acceptable", "report"}:
            raise typer.BadParameter("--require must be conforms, acceptable or report")
        try:
            content = (
                compile_bicep(template)
                if template.suffix.lower() == ".bicep"
                else template.read_bytes()
            )
            request = SolutionAssessmentInput(
                architecture_id=architecture_id,
                version=version,
                baseline_concept_id=baseline_concept,
                baseline_version_id=baseline_version,
                template=json.loads(content),
                parameters=json.loads(parameters.read_text(encoding="utf-8")) if parameters else {},
                commit=commit,
                as_of=date.fromisoformat(as_of) if as_of else None,
            )

            async def execute():
                async with Runtime() as runtime:
                    return await runtime.assessments.assess(request)

            result = asyncio.run(execute())
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
            raise typer.BadParameter(str(error)) from None
        emit(result)
        if not passes(result, cast(Literal["conforms", "acceptable", "report"], require)):
            raise typer.Exit(1)


def register_portfolio(app: typer.Typer, emit: Callable[[Any], None]) -> None:
    portfolio = typer.Typer(help="Read-only portfolio views over registered solutions.")
    app.add_typer(portfolio, name="portfolio")

    def day(value: str | None) -> date | None:
        return date.fromisoformat(value) if value else None

    @portfolio.command("exceptions")
    def exceptions(
        baseline_concept: Annotated[UUID, typer.Option(help="Approved baseline concept ID.")],
        baseline_version: Annotated[UUID, typer.Option(help="Approved baseline version ID.")],
        as_of: Annotated[str | None, typer.Option(help="ISO date.")] = None,
        previous_as_of: Annotated[str | None, typer.Option(help="ISO date to compare.")] = None,
        window_days: Annotated[int, typer.Option(min=0, max=365)] = 30,
        review_rate: Annotated[float, typer.Option(min=0.01, max=1)] = 0.25,
    ) -> None:
        """Report exception exposure, denominators and review signals."""
        try:
            request = ExceptionRiskInput(
                baseline_concept_id=baseline_concept,
                baseline_version_id=baseline_version,
                as_of=day(as_of),
                previous_as_of=day(previous_as_of),
                window_days=window_days,
                review_rate=review_rate,
            )

            async def execute():
                async with Runtime() as runtime:
                    return await runtime.portfolio.exception_risk(request)

            emit(asyncio.run(execute()))
        except (ValueError, ValidationError) as error:
            raise typer.BadParameter(str(error)) from None

    @portfolio.command("impact")
    def impact(
        old_concept: Annotated[UUID, typer.Option(help="Current baseline concept ID.")],
        old_version: Annotated[UUID, typer.Option(help="Current baseline version ID.")],
        new_concept: Annotated[UUID, typer.Option(help="Changed baseline concept ID.")],
        new_version: Annotated[UUID, typer.Option(help="Changed baseline version ID.")],
        stage: Annotated[str, typer.Option(help="proposed or approved.")] = "proposed",
        effective_from: Annotated[str | None, typer.Option(help="ISO date.")] = None,
        as_of: Annotated[str | None, typer.Option(help="ISO date.")] = None,
    ) -> None:
        """Trace a baseline change to affected solutions; drafts are exported, not sent."""
        try:
            request = ChangeImpactInput.model_validate(
                {
                    "old_concept_id": old_concept,
                    "old_version_id": old_version,
                    "new_concept_id": new_concept,
                    "new_version_id": new_version,
                    "stage": stage,
                    "effective_from": day(effective_from),
                    "as_of": day(as_of),
                }
            )

            async def execute():
                async with Runtime() as runtime:
                    return await runtime.portfolio.change_impact(request)

            emit(asyncio.run(execute()))
        except (ValueError, ValidationError) as error:
            raise typer.BadParameter(str(error)) from None


def passes(
    result: SolutionAssessment, require: Literal["conforms", "acceptable", "report"]
) -> bool:
    match require:
        case "conforms":
            return result.conforms
        case "acceptable":
            return result.acceptable_with_exceptions
        case "report":
            return True
