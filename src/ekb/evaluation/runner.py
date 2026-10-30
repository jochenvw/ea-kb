from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydantic_evals import Case as EvalCase
from pydantic_evals import Dataset as EvalDataset
from pydantic_evals.evaluators import EvaluationResult
from pydantic_evals.reporting import ReportCase


async def evaluate_sequential[InputT, OutputT](
    cases: list[EvalCase[InputT, OutputT, None]],
    task: Callable[[InputT], Awaitable[OutputT]],
    *,
    name: str,
) -> list[ReportCase[InputT, OutputT, None]]:
    results: list[ReportCase[InputT, OutputT, None]] = []
    # Pydantic Evals schedules every case in a dataset, even with concurrency set to one.
    for offset in range(0, len(cases), 16):
        batch = cases[offset : offset + 16]
        report = await EvalDataset[InputT, OutputT, None](name=name, cases=batch).evaluate(
            task,
            task_name=name,
            max_concurrency=1,
            progress=False,
            retry_task=None,
            retry_evaluators=None,
        )
        if (
            report.failures
            or any(case.evaluator_failures for case in report.cases)
            or [case.name for case in report.cases] != [case.name for case in batch]
        ):
            raise RuntimeError(f"Pydantic Evals failed while running {name}")
        results.extend(report.cases)
    return results


def serialize_results(
    results: Mapping[str, EvaluationResult[Any]],
) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "name": result.name,
            "value": result.value,
            "reason": str(result.reason) if result.reason is not None else None,
            "evaluator": result.source.name,
            "version": result.evaluator_version,
        }
        for name, result in results.items()
    }
