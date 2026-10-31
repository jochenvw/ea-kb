from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator
from pydantic_evals import Case as EvalCase
from pydantic_evals import Dataset as EvalDataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from ekb.domain.models import (
    Model,
    Scope,
    SemanticType,
    Usage,
    content_hash,
    stable_id,
    total_usage,
)
from ekb.domain.traceability import TraceabilityAssertion, TraceabilitySelection
from ekb.evaluation.dataset import Split
from ekb.ports import TraceabilityMapper

type ProvenanceSplit = Split | Literal["all"]


class ProvenanceSource(Model):
    name: str = Field(min_length=1)
    location: str = Field(min_length=1)


class ProvenanceAssertion(Model):
    statement: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    semantic_type: SemanticType
    source: ProvenanceSource


class ProvenanceCase(Model):
    id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    split: Split
    child_key: str = Field(min_length=1)
    parent_keys: tuple[str, ...] = Field(min_length=2, max_length=10)
    expected_parent_keys: tuple[str, ...] = Field(max_length=3)


class ProvenanceDataset(Model):
    version: str
    review_status: Literal["provisional", "independently_reviewed"]
    assertions: dict[str, ProvenanceAssertion] = Field(min_length=1)
    cases: tuple[ProvenanceCase, ...] = Field(min_length=10)

    @model_validator(mode="after")
    def isolated_splits(self) -> Self:
        if {case.split for case in self.cases} != {"train", "validation", "test"}:
            raise ValueError("Provenance evaluation requires train, validation, and test splits")
        families: dict[str, Split] = {}
        texts: dict[str, Split] = {}
        for case in self.cases:
            if (
                case.child_key not in self.assertions
                or not set(case.parent_keys) <= self.assertions.keys()
            ):
                raise ValueError("Provenance case references an unknown assertion")
            if len(set(case.parent_keys)) != len(case.parent_keys):
                raise ValueError("Parent keys must be unique within a provenance case")
            if not set(case.expected_parent_keys) <= set(case.parent_keys):
                raise ValueError("Expected provenance parents must be supplied candidates")
            if case.family in families and families[case.family] != case.split:
                raise ValueError("Provenance families cannot cross dataset splits")
            families[case.family] = case.split
            normalized = " ".join(self.assertions[case.child_key].statement.casefold().split())
            if normalized in texts and texts[normalized] != case.split:
                raise ValueError("Duplicate child assertions cannot cross dataset splits")
            texts[normalized] = case.split
        return self


class ProvenanceExecution(Model):
    selection: TraceabilitySelection | None = None
    usage: Usage = Field(default_factory=Usage)
    model_calls: int = 0
    error: str | None = None


@dataclass
class ProvenanceQuality(Evaluator[str, ProvenanceExecution, ProvenanceCase]):
    def get_evaluator_version(self) -> str:
        return "provenance-quality-v1"

    def evaluate(
        self, ctx: EvaluatorContext[str, ProvenanceExecution, ProvenanceCase]
    ) -> dict[str, bool | int]:
        if ctx.metadata is None or ctx.output.selection is None:
            return {"exact": False, "tp": 0, "fp": 0, "fn": 0}
        case = ctx.metadata
        by_id = {
            stable_id("provenance-eval", case.id, parent_key): parent_key
            for parent_key in case.parent_keys
        }
        predicted = {
            by_id[link.parent_concept_id]
            for link in ctx.output.selection.links
            if link.parent_concept_id in by_id
        }
        expected = set(case.expected_parent_keys)
        return {
            "exact": not ctx.output.error and predicted == expected,
            "tp": len(predicted & expected),
            "fp": len(predicted - expected),
            "fn": len(expected - predicted),
        }


def load_provenance_dataset(path: Path) -> tuple[bytes, ProvenanceDataset]:
    raw = path.read_bytes()
    return raw, ProvenanceDataset.model_validate_json(raw)


def _assertion(
    case: ProvenanceCase,
    key: str,
    value: ProvenanceAssertion,
) -> TraceabilityAssertion:
    return TraceabilityAssertion(
        concept_id=stable_id("provenance-eval", case.id, key),
        version_id=stable_id("provenance-eval-version", case.id, key),
        occurrence_id=stable_id("provenance-eval-occurrence", case.id, key),
        statement=value.statement,
        excerpt=value.excerpt,
        semantic_type=value.semantic_type,
        scope=(
            Scope(platform="Azure")
            if value.semantic_type is SemanticType.PLATFORM_REQUIREMENT
            else Scope()
        ),
    )


async def _run_case(
    case_id: str,
    *,
    cases: dict[str, ProvenanceCase],
    assertions: dict[str, ProvenanceAssertion],
    mapper: TraceabilityMapper,
    completed: dict[str, ProvenanceExecution],
) -> ProvenanceExecution:
    case = cases[case_id]
    try:
        result = await mapper.map(
            _assertion(case, case.child_key, assertions[case.child_key]),
            tuple(
                _assertion(case, parent_key, assertions[parent_key])
                for parent_key in case.parent_keys
            ),
        )
        execution = ProvenanceExecution(
            selection=result.selection,
            usage=result.usage,
            model_calls=result.model_calls,
            error=None if result.completed else result.status,
        )
    except Exception as error:
        execution = ProvenanceExecution(error=type(error).__name__)
    completed[case_id] = execution
    return execution


async def evaluate_provenance(
    dataset_path: Path,
    output: Path,
    mapper: TraceabilityMapper,
    *,
    split: ProvenanceSplit = "test",
    max_cases: int = 100,
    max_concurrency: int = 4,
    history_dir: Path = Path("data") / "evaluation-history",
) -> dict[str, Any]:
    raw, dataset = await asyncio.to_thread(load_provenance_dataset, dataset_path)
    cases = [case for case in dataset.cases if split == "all" or case.split == split][:max_cases]
    if not cases:
        raise ValueError("No provenance evaluation cases match the selected split")
    by_id = {case.id: case for case in cases}
    completed: dict[str, ProvenanceExecution] = {}
    eval_dataset = EvalDataset[str, ProvenanceExecution, ProvenanceCase](
        name=f"control-traceability-{dataset.version}-{split}",
        cases=[
            EvalCase(
                name=case.id,
                inputs=case.id,
                metadata=case,
                evaluators=[ProvenanceQuality()],
            )
            for case in cases
        ],
    )
    framework = await eval_dataset.evaluate(
        lambda case_id: _run_case(
            case_id,
            cases=by_id,
            assertions=dataset.assertions,
            mapper=mapper,
            completed=completed,
        ),
        max_concurrency=max_concurrency,
        progress=False,
        task_name="ekb-control-traceability",
        metadata={
            "dataset_version": dataset.version,
            "dataset_sha256": content_hash(raw),
            "mapper_fingerprint": mapper.fingerprint,
            "split": split,
        },
    )
    failures = {failure.name for failure in framework.failures}
    failures.update(case.name for case in framework.cases if case.evaluator_failures)
    tp = fp = fn = exact = 0
    case_reports = []
    for case in cases:
        execution = completed.get(case.id, ProvenanceExecution(error="missing_result"))
        if execution.error is not None:
            failures.add(case.id)
        by_parent = {
            stable_id("provenance-eval", case.id, parent_key): parent_key
            for parent_key in case.parent_keys
        }
        predicted = {
            by_parent[link.parent_concept_id]
            for link in (execution.selection.links if execution.selection else ())
            if link.parent_concept_id in by_parent
        }
        expected = set(case.expected_parent_keys)
        tp += len(predicted & expected)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
        exact += predicted == expected and execution.error is None and case.id not in failures
        case_reports.append(
            {
                "id": case.id,
                "expected": sorted(expected),
                "predicted": sorted(predicted),
                "error": execution.error,
            }
        )
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    model_calls = sum(execution.model_calls for execution in completed.values())
    usage = total_usage([execution.usage for execution in completed.values()])
    report: dict[str, Any] = {
        "passed": not failures and precision >= 0.9 and recall >= 0.9,
        "dataset_version": dataset.version,
        "dataset_sha256": content_hash(raw),
        "review_status": dataset.review_status,
        "split": split,
        "cases": len(cases),
        "exact_cases": exact,
        "precision": precision,
        "recall": recall,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "failures": sorted(failures),
        "case_results": case_reports,
        "mapper_fingerprint": mapper.fingerprint,
        "model_calls": model_calls,
        "usage": usage.model_dump(mode="json"),
    }
    serialized = json.dumps(report, indent=2) + "\n"
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, serialized, encoding="utf-8")
    await asyncio.to_thread(history_dir.mkdir, parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    history = history_dir / f"provenance-{stamp}-{content_hash(serialized.encode())[:8]}.json"
    await asyncio.to_thread(history.write_text, serialized, encoding="utf-8")
    report["history"] = str(history)
    return report
