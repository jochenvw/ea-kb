from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import TypeAdapter

from ekb.adapters.inference import OpenAIInference, RulesInference
from ekb.config import Settings
from ekb.domain.models import content_hash
from ekb.evaluation.dataset import Split, load_dataset
from ekb.evaluation.distillation import export_training
from ekb.evaluation.frugal import qualify
from ekb.evaluation.suite import run_suite
from ekb.runtime import Runtime

DATASET = Path("evals") / "teacher-v2.json"
GATES = Path("evals") / "gates.json"


def register(app: typer.Typer, evaluations: typer.Typer, emit: Callable[[Any], None]) -> None:
    def execute(operation):
        try:
            result = asyncio.run(operation())
        except ValueError as error:
            raise typer.BadParameter(str(error)) from None
        emit(result)
        if result.get("passed") is False:
            raise typer.Exit(1)

    @app.command("test")
    def test(
        live: Annotated[
            bool, typer.Option(help="Explicitly enable configured paid model calls.")
        ] = False,
        unit_only: Annotated[bool, typer.Option()] = False,
        ui: Annotated[
            bool,
            typer.Option(help="Also check/build the frontend and run Playwright (needs Node)."),
        ] = False,
        output: Annotated[Path, typer.Option()] = Path("data") / "suite",
        dataset: Annotated[Path, typer.Option()] = DATASET,
    ):
        """Run code tests and source-grounded evaluations with a single exit status."""

        async def operation():
            return await run_suite(
                Settings(), dataset, GATES, output, live=live, unit_only=unit_only, ui=ui
            )

        execute(operation)

    @evaluations.command("qualify")
    def evaluate(
        live: Annotated[bool, typer.Option()] = False,
        split: Annotated[str, typer.Option()] = "test",
        dataset: Annotated[Path, typer.Option()] = DATASET,
        output: Annotated[Path, typer.Option()] = Path("data") / "qualification.json",
        max_cases: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    ):
        """Measure held-out student and embedding quality; never silently qualify offline rules."""

        async def operation():
            selected = TypeAdapter(Split).validate_python(split)
            if live:
                settings = Settings()
                if settings.inference != "openai":
                    raise ValueError("Set EKB_INFERENCE=openai for live qualification")
                async with Runtime(settings) as runtime:
                    return await qualify(
                        dataset,
                        GATES,
                        output,
                        runtime.inference,
                        runtime.verifier,
                        runtime.embeddings,
                        live=True,
                        split=selected,
                        max_cases=max_cases,
                        embedding_input_price=settings.embedding_input_price,
                    )
            return await qualify(
                dataset, GATES, output, RulesInference(), split=selected, max_cases=max_cases
            )

        execute(operation)

    @evaluations.command("distill")
    def distill(
        output: Annotated[Path, typer.Option()],
        teacher_model: Annotated[str | None, typer.Option()] = None,
        dataset: Annotated[Path, typer.Option()] = DATASET,
        max_cases: Annotated[int, typer.Option(min=1, max=1000)] = 32,
    ):
        """Export training split only; --teacher-model opts into fresh teacher API labeling."""

        async def operation():
            if not teacher_model:
                return await export_training(dataset, output, max_cases=max_cases)
            teacher = OpenAIInference(Settings().model_copy(update={"model_id": teacher_model}))
            try:
                return await export_training(dataset, output, teacher, max_cases)
            finally:
                await teacher.close()

        execute(operation)

    @evaluations.command("verify-corpus")
    def verify(dataset: Annotated[Path, typer.Option()] = DATASET):
        """Verify source hashes and teacher quotations against retained original documents."""

        async def operation():
            labels = await asyncio.to_thread(load_dataset, dataset)
            async with Runtime() as runtime:
                revisions = []
                async with runtime.database() as uow:
                    sources = {s.item.location: s for s in await uow.sources.list()}
                    for reference in labels.sources:
                        source = sources.get(reference.url)
                        if source is None or source.revision_id is None:
                            raise ValueError("Dataset source is not retained: " + reference.id)
                        revision = await uow.sources.revision(source.revision_id)
                        if revision is None or revision.content_hash != reference.sha256:
                            raise ValueError("Retained original hash changed: " + reference.id)
                        revisions.append((reference, revision))
                checked = 0
                for reference, revision in revisions:
                    raw = await runtime.artifacts.get(revision.artifact_key)
                    if content_hash(raw) != reference.sha256:
                        raise ValueError("Retained artifact hash changed: " + reference.id)
                    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
                    for seed in labels.seeds:
                        if seed.source == reference.id:
                            if seed.quote not in text:
                                raise ValueError("Teacher quotation is not in original: " + seed.id)
                            checked += 1
            return {"passed": True, "sources": len(labels.sources), "verified_quotes": checked}

        execute(operation)
