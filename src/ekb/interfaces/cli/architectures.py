from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer
import yaml
from pydantic import TypeAdapter, ValidationError

from ekb.domain.architectures import ArchitectureEvidence, ArchitectureManifest
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
