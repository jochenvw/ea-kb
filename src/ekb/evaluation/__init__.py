from __future__ import annotations

import asyncio
import json
import re
from importlib.metadata import version
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_evals import Case as EvalCase
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from ekb.domain.grounding import validate_evidence
from ekb.domain.models import (
    Candidate,
    Extraction,
    InferenceResult,
    Modality,
    Model,
    Segment,
    SemanticType,
    content_hash,
    now,
    stable_id,
)
from ekb.evaluation.runner import evaluate_sequential, serialize_results
from ekb.ports import InferencePort


class Expected(Model):
    semantic_type: SemanticType | None = None
    excerpt: str
    modality: Modality = Modality.UNSPECIFIED
    scope: dict[str, Any] = Field(default_factory=dict)
    duplicate_group: str | None = None


class Case(Model):
    id: str
    source: str
    text: str
    location: dict[str, Any] = Field(default_factory=dict)
    expected: list[Expected] = Field(default_factory=list)


def ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def qualifier_preserved(candidate: Candidate, expected: Expected) -> bool:
    for qualifier in ("not", "unless", "except", "only", "without"):
        if re.search(rf"\b{qualifier}\b", expected.excerpt, re.I) and not re.search(
            rf"\b{qualifier}\b", candidate.statement, re.I
        ):
            return False
    return True


def score(cases: list[Case], predictions: list[Extraction]) -> dict[str, Any]:
    totals = {kind: {"tp": 0, "fp": 0, "fn": 0} for kind in (*SemanticType, None)}
    modality_correct = modality_total = scope_correct = scope_total = 0
    known_scope_correct = known_scope_total = grounded = extracted = false_mandates = 0
    duplicate_candidates: list[tuple[str, str | None]] = []
    expected_duplicates: list[str] = []
    for case, prediction in zip(cases, predictions, strict=True):
        used: set[int] = set()
        for expected in case.expected:
            if expected.duplicate_group:
                expected_duplicates.append(expected.duplicate_group)
        for candidate in prediction.candidates:
            extracted += 1
            grounded += int(candidate.excerpt in case.text)
            matches = [
                (index, expected)
                for index, expected in enumerate(case.expected)
                if index not in used
                and (
                    expected.semantic_type is None
                    or candidate.semantic_type == expected.semantic_type
                )
                and expected.excerpt in candidate.excerpt
                and qualifier_preserved(candidate, expected)
            ]
            match = matches[0] if matches else None
            category = match[1].semantic_type if match else candidate.semantic_type
            totals[category]["tp" if match else "fp"] += 1
            if match:
                index, expected = match
                used.add(index)
                if candidate.semantic_type != SemanticType.STRATEGIC_GOAL:
                    modality_total += 1
                    modality_correct += candidate.modality == expected.modality
                actual_scope = candidate.scope.model_dump(mode="json", exclude_none=True)
                scope_total += 1
                scope_correct += actual_scope == expected.scope
                if expected.scope:
                    known_scope_total += 1
                    known_scope_correct += actual_scope == expected.scope
                if (
                    candidate.modality == Modality.MANDATED
                    and expected.modality != Modality.MANDATED
                ):
                    false_mandates += 1
            elif candidate.modality == Modality.MANDATED:
                false_mandates += 1
            duplicate_key = json.dumps(
                [
                    candidate.semantic_type,
                    re.sub(r"\s+", " ", candidate.statement).strip().casefold(),
                    candidate.modality,
                    candidate.scope.model_dump(mode="json"),
                ],
                sort_keys=True,
            )
            duplicate_candidates.append(
                (duplicate_key, match[1].duplicate_group if match else None)
            )
        for index, expected in enumerate(case.expected):
            if index not in used:
                totals[expected.semantic_type]["fn"] += 1
    duplicates_tp = duplicates_fp = 0
    for index, (key, group) in enumerate(duplicate_candidates):
        for other_key, other_group in duplicate_candidates[index + 1 :]:
            if key == other_key:
                if group and group == other_group:
                    duplicates_tp += 1
                else:
                    duplicates_fp += 1
    duplicate_pairs = sum(
        1
        for index, group in enumerate(expected_duplicates)
        for other in expected_duplicates[index + 1 :]
        if group == other
    )
    result: dict[str, Any] = {}
    for kind, counts in totals.items():
        result[kind.value if kind else "unclassified"] = {
            **counts,
            "precision": ratio(counts["tp"], counts["tp"] + counts["fp"]),
            "recall": ratio(counts["tp"], counts["tp"] + counts["fn"]),
        }
    result.update(
        {
            "modality_accuracy": ratio(modality_correct, modality_total),
            "scope_accuracy": ratio(scope_correct, scope_total),
            "known_scope_accuracy": ratio(known_scope_correct, known_scope_total),
            "evidence_grounding": ratio(grounded, extracted),
            "false_mandatory_requirements": false_mandates,
            "weighted_error": sum(v["fp"] + v["fn"] for v in totals.values()) + false_mandates * 5,
            "duplicate_precision": ratio(duplicates_tp, duplicates_tp + duplicates_fp),
            "duplicate_recall": ratio(duplicates_tp, duplicate_pairs),
        }
    )
    return result


class _ExtractionOutcome(Model):
    candidate_count: int = 0
    error: str | None = None


class _ExtractionEvaluator(Evaluator[str, _ExtractionOutcome, None]):
    def __init__(self, cases: dict[str, Case], results: dict[str, InferenceResult]):
        # Non-dataclass state is excluded from framework evaluator specifications.
        self._cases = cases
        self._results = results

    def evaluate(
        self, ctx: EvaluatorContext[str, _ExtractionOutcome, None]
    ) -> dict[str, bool | int]:
        result = self._results.get(ctx.inputs)
        metrics = score(
            [self._cases[ctx.inputs]], [result.extraction if result is not None else Extraction()]
        )
        categories = [value for value in metrics.values() if isinstance(value, dict)]
        tp = sum(value["tp"] for value in categories)
        fp = sum(value["fp"] for value in categories)
        fn = sum(value["fn"] for value in categories)
        return {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "false_mandates": metrics["false_mandatory_requirements"],
            "returned_candidates": ctx.output.candidate_count,
            "case_exact_match": ctx.output.error is None
            and fp == 0
            and fn == 0
            and metrics["scope_accuracy"] in {None, 1}
            and metrics["modality_accuracy"] in {None, 1}
            and metrics["false_mandatory_requirements"] == 0,
            "evidence_grounded": ctx.output.error is None,
        }


async def evaluate(dataset: Path, output: Path, inference: InferencePort) -> dict[str, Any]:
    raw = await asyncio.to_thread(dataset.read_bytes)
    cases = [Case.model_validate(case) for case in json.loads(raw)["cases"]]
    cases_by_key = {f"extraction-{index}": case for index, case in enumerate(cases)}
    results: dict[str, InferenceResult] = {}
    evaluator = _ExtractionEvaluator(cases_by_key, results)

    async def extract(key: str) -> _ExtractionOutcome:
        case = cases_by_key[key]
        segment = Segment(
            id=stable_id("golden", case.id),
            revision_id=stable_id("golden", case.source),
            ordinal=0,
            text=case.text,
            location=case.location,
        )
        try:
            result = await inference.extract(segment)
            validate_evidence(result.extraction, segment)
            outcome = _ExtractionOutcome(candidate_count=len(result.extraction.candidates))
            results[key] = result
            return outcome
        except Exception as error:
            return _ExtractionOutcome(error=type(error).__name__)

    evaluated = await evaluate_sequential(
        [
            EvalCase[str, _ExtractionOutcome, None](name=key, inputs=key, evaluators=[evaluator])
            for key in cases_by_key
        ],
        extract,
        name="grounded-extraction",
    )
    predictions: list[Extraction] = []
    documents: list[dict[str, Any]] = []
    for case, evaluated_case in zip(cases, evaluated, strict=True):
        result = results.get(evaluated_case.inputs)
        if result is not None:
            predictions.append(result.extraction)
            document = {"id": case.id, "result": result.model_dump(mode="json")}
        else:
            predictions.append(Extraction())
            document = {"id": case.id, "error": evaluated_case.output.error}
        document.update(
            {
                "latency_ms": evaluated_case.task_duration * 1000,
                "scores": serialize_results(evaluated_case.scores),
                "assertions": serialize_results(evaluated_case.assertions),
            }
        )
        documents.append(document)
    metrics = score(cases, predictions)
    report = {
        "recorded_at": now().isoformat(),
        "dataset_sha256": content_hash(raw),
        "provider": inference.capabilities.model_id,
        "prompt_version": inference.prompt_version,
        "offline_baseline": inference.capabilities.model_id.startswith("offline-"),
        "evaluation_runner": "pydantic-evals",
        "evaluation_runner_version": version("pydantic-evals"),
        "metrics": metrics,
        "documents": documents,
        "mean_latency_ms": sum(d["latency_ms"] for d in documents) / len(documents),
        "limitations": [
            "Small manually labeled corpus; not a compliance benchmark.",
            "Evidence-aligned matching and qualifier checks do not prove semantic entailment.",
            "Unknown scope is distinct from known scope; inspect known_scope_accuracy separately.",
            "Unclassified gold measures extraction only; use skill evals for ontology classes.",
            "No small-versus-strong model comparison is claimed without actual model runs.",
        ],
    }
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, json.dumps(report, indent=2), encoding="utf-8")
    summary = (
        f"EKB evaluation: {report['provider']} ({len(cases)} cases)\n"
        f"Strategic goal precision/recall: {metrics['StrategicGoal']['precision']} / "
        f"{metrics['StrategicGoal']['recall']}\n"
        f"Unclassified precision/recall: {metrics['unclassified']['precision']} / "
        f"{metrics['unclassified']['recall']}\n"
        f"Modality accuracy: {metrics['modality_accuracy']}\n"
        f"Scope accuracy / known scope: {metrics['scope_accuracy']} / "
        f"{metrics['known_scope_accuracy']}\n"
        f"Evidence grounding: {metrics['evidence_grounding']}\n"
        f"False mandatory requirements (5x penalty): {metrics['false_mandatory_requirements']}\n"
        f"Duplicate precision/recall: {metrics['duplicate_precision']} / "
        f"{metrics['duplicate_recall']}\n"
        f"Mean latency per document: {report['mean_latency_ms']:.2f} ms\n"
        "Offline rule results are not LLM benchmarks. Null metrics mean no applicable cases.\n"
    )
    await asyncio.to_thread(output.with_suffix(".txt").write_text, summary, encoding="utf-8")
    return report
