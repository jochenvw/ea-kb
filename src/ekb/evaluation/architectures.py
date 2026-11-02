from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_evals import Case as EvalCase
from pydantic_evals import Dataset as EvalDataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from ekb.domain.architectures import (
    ArchitectureControl,
    ArchitectureEvidence,
    AssessmentOutcome,
    coverage_outcome,
)
from ekb.domain.models import Model, content_hash


class ArchitectureEvaluationCase(Model):
    id: str
    controls: tuple[ArchitectureControl, ...] = Field(min_length=1)
    evidence: tuple[UUID, ...] = ()
    as_of: date
    expected_outcomes: tuple[AssessmentOutcome, ...]
    expected_conforms: bool

    @model_validator(mode="after")
    def valid_case(self) -> Self:
        if len({control.concept_id for control in self.controls}) != len(self.controls):
            raise ValueError("Evaluation controls must be unique")
        if len(self.expected_outcomes) != len(self.controls):
            raise ValueError("Expected outcomes must align with controls")
        return self


class ArchitectureEvaluationDataset(Model):
    version: str
    review_status: Literal["provisional", "independently_reviewed"]
    cases: tuple[ArchitectureEvaluationCase, ...] = Field(min_length=1)


class ArchitectureEvaluationOutput(Model):
    outcomes: tuple[AssessmentOutcome, ...]
    conforms: bool


@dataclass
class ArchitectureQuality(Evaluator[str, ArchitectureEvaluationOutput, ArchitectureEvaluationCase]):
    def get_evaluator_version(self) -> str:
        return "architecture-assessment-v1"

    def evaluate(
        self,
        ctx: EvaluatorContext[str, ArchitectureEvaluationOutput, ArchitectureEvaluationCase],
    ) -> dict[str, bool | int]:
        case = ctx.metadata
        if case is None:
            return {"exact": False, "incorrect_controls": 1}
        incorrect = sum(
            actual is not expected
            for actual, expected in zip(ctx.output.outcomes, case.expected_outcomes, strict=True)
        )
        return {
            "exact": incorrect == 0 and ctx.output.conforms is case.expected_conforms,
            "incorrect_controls": incorrect,
        }


async def evaluate_architectures(
    dataset_path: Path = Path("evals") / "architecture-assessment-v1.json",
    output: Path = Path("data") / "architecture-assessment.json",
) -> dict[str, Any]:
    raw = await asyncio.to_thread(dataset_path.read_bytes)
    dataset = ArchitectureEvaluationDataset.model_validate_json(raw)
    by_id = {case.id: case for case in dataset.cases}
    framework = await EvalDataset[str, ArchitectureEvaluationOutput, ArchitectureEvaluationCase](
        name=f"solution-architecture-{dataset.version}",
        cases=[
            EvalCase(
                name=case.id,
                inputs=case.id,
                metadata=case,
                evaluators=[ArchitectureQuality()],
            )
            for case in dataset.cases
        ],
    ).evaluate(
        lambda case_id: _evaluate_case(by_id[case_id]),
        task_name="ekb-solution-architecture-assessment",
        max_concurrency=1,
        progress=False,
        retry_task=None,
        retry_evaluators=None,
        metadata={
            "dataset_version": dataset.version,
            "dataset_sha256": content_hash(raw),
        },
    )
    failures = {failure.name for failure in framework.failures}
    cases = []
    for report in framework.cases:
        failures.update(f"{report.name}:{failure.name}" for failure in report.evaluator_failures)
        result = report.assertions.get("exact")
        if result is None or result.value is not True:
            failures.add(report.name)
        cases.append(
            {
                "id": report.name,
                "exact": bool(result and result.value is True),
            }
        )
    summary = {
        "version": dataset.version,
        "review_status": dataset.review_status,
        "dataset_sha256": content_hash(raw),
        "cases": cases,
        "passed": not failures and len(cases) == len(dataset.cases),
        "failures": sorted(failures),
    }
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text,
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


async def _evaluate_case(
    case: ArchitectureEvaluationCase,
) -> ArchitectureEvaluationOutput:
    evidence_ids = set(case.evidence)
    outcomes = tuple(
        coverage_outcome(
            control,
            ArchitectureEvidence(
                concept_id=control.concept_id,
                summary="Synthetic implementation evidence.",
            )
            if control.concept_id in evidence_ids
            else None,
            case.as_of,
        )
        for control in case.controls
    )
    return ArchitectureEvaluationOutput(
        outcomes=outcomes,
        conforms=all(outcome is not AssessmentOutcome.MISSING for outcome in outcomes),
    )
