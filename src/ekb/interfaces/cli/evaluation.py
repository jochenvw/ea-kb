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
from ekb.evaluation.goals import completed_strategies, evaluate_goals, report_latest
from ekb.evaluation.skills import SkillSelection, evaluate_skills
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
                if settings.inference not in {"openai", "agent-framework"}:
                    raise ValueError("Select agent-framework or openai for live qualification")
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

    @evaluations.command("skills")
    def skills(
        skill: Annotated[str, typer.Option()] = "both",
        live: Annotated[bool, typer.Option()] = False,
        split: Annotated[str, typer.Option()] = "test",
        dataset: Annotated[Path, typer.Option()] = Path("evals") / "skills-v1.json",
        output: Annotated[Path, typer.Option()] = Path("data") / "skill-evaluation.json",
        max_cases: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    ):
        """Evaluate labeling and gold-routed extraction independently; paid calls require --live."""

        async def operation():
            selection = TypeAdapter(SkillSelection).validate_python(skill)
            selected = TypeAdapter(Split).validate_python(split)
            if live:
                settings = Settings()
                if settings.inference not in {"openai", "agent-framework"}:
                    raise ValueError("Select agent-framework or openai for live skill evaluation")
                async with Runtime(settings) as runtime:
                    return await evaluate_skills(
                        dataset,
                        GATES,
                        output,
                        runtime.inference,
                        skill=selection,
                        split=selected,
                        live=True,
                        max_cases=max_cases,
                    )
            return await evaluate_skills(
                dataset,
                GATES,
                output,
                RulesInference(),
                skill=selection,
                split=selected,
                max_cases=max_cases,
            )

        execute(operation)

    @evaluations.command("documents")
    def documents(
        live: Annotated[
            bool, typer.Option(help="Explicitly allow configured model calls.")
        ] = False,
        verify_sources: Annotated[
            bool, typer.Option(help="Verify retained source hashes and cited PDF/text passages.")
        ] = False,
        dataset: Annotated[Path, typer.Option()] = Path("evals") / "documents-v1.json",
        output: Annotated[Path, typer.Option()] = Path("data") / "document-evaluation.json",
    ):
        """Evaluate document coverage, mixed ontology labels and gold-routed extraction."""

        async def operation():
            if live:
                settings = Settings()
                if settings.inference not in {"openai", "agent-framework"}:
                    raise ValueError(
                        "Select agent-framework or openai for live document evaluation"
                    )
                async with Runtime(settings) as runtime:
                    return await evaluate_skills(
                        dataset,
                        GATES,
                        output,
                        runtime.inference,
                        split="all",
                        live=True,
                        verify_sources=verify_sources,
                    )
            return await evaluate_skills(
                dataset, GATES, output, RulesInference(), split="all", verify_sources=verify_sources
            )

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

    @evaluations.command("goals")
    def goals(
        source: Annotated[
            Path, typer.Option(help="Source PDF; its hash must match the reference dataset.")
        ] = Path("data")
        / "test-corpus"
        / "public"
        / "corporate_strategy"
        / "shell-annual-2025.pdf",
        dataset: Annotated[Path, typer.Option()] = Path("evals")
        / "goals"
        / "shell-annual-2025.json",
        output: Annotated[Path, typer.Option(help="Evaluation history and extraction cache.")] = (
            Path("data") / "goal-evaluations"
        ),
        strategy: Annotated[
            str, typer.Option(help="whole-document, section-aware, or hierarchical.")
        ] = "section-aware",
        compare_strategies: Annotated[
            bool, typer.Option(help="Run all three strategies for a direct comparison.")
        ] = False,
        live: Annotated[
            bool, typer.Option(help="Explicitly allow configured provider/model calls.")
        ] = False,
        model_profile: Annotated[
            Path,
            typer.Option(help="Settings YAML; live model calls may incur cost."),
        ] = Path("config") / "local.yaml",
        max_model_calls: Annotated[int, typer.Option(min=1, max=100000)] = 10000,
    ) -> None:
        """Evaluate enterprise strategic-goal extraction against source-audited references."""

        async def operation():
            settings = Settings(config_file=model_profile)
            if settings.inference != "rules" and not live:
                raise ValueError("Pass --live to enable configured provider/model calls")
            strategies = (
                ("whole_document", "section_aware", "hierarchical")
                if compare_strategies
                else completed_strategies(strategy)
            )
            configuration = {
                "profile": str(model_profile),
                "inference_provider": settings.inference,
                "labeling_model_id": settings.labeling_model_id or settings.model_id,
                "temperature": settings.model_temperature,
                "max_output_tokens": settings.model_max_output_tokens,
                "max_token_parameter": settings.model_max_token_parameter,
                "model_retries": settings.model_retries,
                "structured_output": settings.model_structured_output,
            }
            calls_per_segment = (
                2 if settings.inference == "rules" else 6 * (settings.model_retries + 1)
            )
            async with Runtime(settings) as runtime:
                report = await evaluate_goals(
                    source,
                    dataset,
                    output,
                    runtime.parser,
                    runtime.inference,
                    strategies=strategies,
                    configuration=configuration,
                    max_model_calls=max_model_calls,
                    max_calls_per_segment=calls_per_segment,
                )
            return {
                "status": report["status"],
                "run_id": report["run_id"],
                "model": report["model"],
                "strategies": {
                    name: {
                        "metrics": result["metrics"],
                        "model_calls": result["model_calls"],
                        "current_run_model_calls": result["current_run_model_calls"],
                        "model_call_count_complete": result["model_call_count_complete"],
                        "model_call_budget_consumed": result["model_call_budget_consumed"],
                        "unaccounted_model_call_reserve": result["unaccounted_model_call_reserve"],
                        "current_run_cost": result["current_run_cost"],
                        "elapsed_ms": result["elapsed_ms"],
                    }
                    for name, result in report["strategies"].items()
                },
                "json_report": report["json_report"],
                "markdown_report": report["markdown_report"],
            }

        try:
            emit(asyncio.run(operation()))
        except (ValueError, OSError) as error:
            raise typer.BadParameter(str(error)) from None

    @evaluations.command("report")
    def goal_report(
        latest: Annotated[bool, typer.Option(help="Show the latest strategic-goal report.")] = True,
        output: Annotated[Path, typer.Option(help="Evaluation history directory.")] = (
            Path("data") / "goal-evaluations"
        ),
    ) -> None:
        """Display the most recent strategic-goal evaluation report."""

        if not latest:
            raise typer.BadParameter("Only --latest is supported")

        async def operation():
            report, markdown_path = await report_latest(output)
            markdown = await asyncio.to_thread(markdown_path.read_text, encoding="utf-8")
            return {
                "run_id": report["run_id"],
                "status": report["status"],
                "json_report": str(markdown_path.with_suffix(".json")),
                "markdown": markdown,
            }

        try:
            emit(asyncio.run(operation()))
        except (ValueError, OSError) as error:
            raise typer.BadParameter(str(error)) from None
