from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from ekb.domain.grounding import validate_evidence
from ekb.domain.models import (
    Candidate,
    ConceptKind,
    Extraction,
    Modality,
    Model,
    Segment,
    content_hash,
    now,
    stable_id,
)
from ekb.ports import InferencePort


class Expected(Model):
    kind: ConceptKind
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
    totals = {kind: {"tp": 0, "fp": 0, "fn": 0} for kind in ConceptKind}
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
                and candidate.kind == expected.kind
                and expected.excerpt in candidate.excerpt
                and qualifier_preserved(candidate, expected)
            ]
            match = matches[0] if matches else None
            totals[candidate.kind]["tp" if match else "fp"] += 1
            if match:
                index, expected = match
                used.add(index)
                if candidate.kind == ConceptKind.REQUIREMENT:
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
                    candidate.kind,
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
                totals[expected.kind]["fn"] += 1
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
        result[kind.value] = {
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


async def evaluate(dataset: Path, output: Path, inference: InferencePort) -> dict[str, Any]:
    raw = await asyncio.to_thread(dataset.read_bytes)
    cases = [Case.model_validate(case) for case in json.loads(raw)["cases"]]
    predictions: list[Extraction] = []
    documents: list[dict[str, Any]] = []
    for case in cases:
        segment = Segment(
            id=stable_id("golden", case.id),
            revision_id=stable_id("golden", case.source),
            ordinal=0,
            text=case.text,
            location=case.location,
        )
        started = time.monotonic()
        try:
            result = await inference.extract(segment)
            validate_evidence(result.extraction, segment)
            predictions.append(result.extraction)
            documents.append(
                {
                    "id": case.id,
                    "result": result.model_dump(mode="json"),
                    "latency_ms": (time.monotonic() - started) * 1000,
                }
            )
        except Exception as error:
            predictions.append(Extraction())
            documents.append(
                {
                    "id": case.id,
                    "error": type(error).__name__,
                    "latency_ms": (time.monotonic() - started) * 1000,
                }
            )
    metrics = score(cases, predictions)
    report = {
        "recorded_at": now().isoformat(),
        "dataset_sha256": content_hash(raw),
        "provider": inference.capabilities.model_id,
        "prompt_version": inference.prompt_version,
        "offline_baseline": inference.capabilities.model_id.startswith("offline-"),
        "metrics": metrics,
        "documents": documents,
        "mean_latency_ms": sum(d["latency_ms"] for d in documents) / len(documents),
        "limitations": [
            "Small manually labeled corpus; not a compliance benchmark.",
            "Evidence-aligned matching and qualifier checks do not prove semantic entailment.",
            "Unknown scope is distinct from known scope; inspect known_scope_accuracy separately.",
            "No small-versus-strong model comparison is claimed without actual model runs.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, json.dumps(report, indent=2), encoding="utf-8")
    summary = (
        f"EKB evaluation: {report['provider']} ({len(cases)} cases)\n"
        f"Objective precision/recall: {metrics['objective']['precision']} / "
        f"{metrics['objective']['recall']}\n"
        f"Requirement precision/recall: {metrics['requirement']['precision']} / "
        f"{metrics['requirement']['recall']}\n"
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
