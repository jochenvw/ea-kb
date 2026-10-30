from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from ekb.domain.models import (
    ContentLabeling,
    Extraction,
    Model,
    Segment,
    SemanticType,
    Usage,
    content_hash,
    stable_id,
    total_usage,
)
from ekb.domain.skills import validate_labeling, validate_scoped_extraction
from ekb.evaluation import Case, Expected, qualifier_preserved, ratio, score
from ekb.evaluation.dataset import Source, Split, write_json
from ekb.evaluation.frugal import Gates
from ekb.ports import IngestionSkills

type SkillSelection = Literal["labeling", "extraction", "both"]


class SkillCase(Model):
    id: str
    family: str
    split: Split
    text: str
    labeling: ContentLabeling
    expected: Extraction
    source: Source | None = None

    def segment(self) -> Segment:
        return Segment(
            id=stable_id("skill-case", self.id),
            revision_id=stable_id("skill-family", self.family),
            ordinal=0,
            text=self.text,
        )


class SkillDataset(Model):
    version: str
    review_status: Literal["provisional", "independently_reviewed"]
    cases: tuple[SkillCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def isolated_grounded_cases(self) -> Self:
        splits: dict[str, Split] = {}
        ids: set[str] = set()
        for case in self.cases:
            if case.id in ids:
                raise ValueError("Skill case IDs must be unique")
            ids.add(case.id)
            keys = [case.family, " ".join(case.text.casefold().split())]
            if case.source:
                if case.source.split != case.split or case.source.group != case.family:
                    raise ValueError("Skill cases must retain their attributed source split/family")
                keys.extend([case.source.url, case.source.sha256])
            for key in keys:
                if key in splits and splits[key] != case.split:
                    raise ValueError("Document families and duplicate text cannot cross splits")
                splits[key] = case.split
            validate_scoped_extraction(case.expected, case.segment(), case.labeling)
        if {case.split for case in self.cases} != {"train", "validation", "test"}:
            raise ValueError("Skill dataset requires train, validation and test cases")
        return self


def _counts(expected: int, actual: int, matched: int) -> dict:
    return {
        "tp": matched,
        "fp": actual - matched,
        "fn": expected - matched,
        "precision": ratio(matched, actual),
        "recall": ratio(matched, expected),
    }


def label_metrics(cases: list[SkillCase], predictions: list[ContentLabeling]) -> dict:
    expected_count = actual_count = matched = 0
    for case, prediction in zip(cases, predictions, strict=True):
        used: set[int] = set()
        expected_count += len(case.labeling.labels)
        actual_count += len(prediction.labels)
        for label in prediction.labels:
            match = next(
                (
                    index
                    for index, expected in enumerate(case.labeling.labels)
                    if index not in used
                    and label.semantic_type == expected.semantic_type
                    and label.scope == expected.scope
                    and expected.excerpt in label.excerpt
                ),
                None,
            )
            if match is not None:
                used.add(match)
                matched += 1
    return _counts(expected_count, actual_count, matched)


def extraction_metrics(cases: list[SkillCase], predictions: list[Extraction]) -> dict:
    expected_count = actual_count = matched = 0
    for case, prediction in zip(cases, predictions, strict=True):
        used: set[int] = set()
        expected_count += len(case.expected.candidates)
        actual_count += len(prediction.candidates)
        for candidate in prediction.candidates:
            match = next(
                (
                    index
                    for index, expected in enumerate(case.expected.candidates)
                    if index not in used
                    and candidate.semantic_type == expected.semantic_type
                    and candidate.scope == expected.scope
                    and candidate.modality == expected.modality
                    and expected.excerpt in candidate.excerpt
                    and qualifier_preserved(
                        candidate,
                        Expected(semantic_type=expected.semantic_type, excerpt=expected.excerpt),
                    )
                ),
                None,
            )
            if match is not None:
                used.add(match)
                matched += 1
    return _counts(expected_count, actual_count, matched)


async def evaluate_skills(
    dataset_path: Path,
    gates_path: Path,
    output: Path,
    inference: IngestionSkills,
    *,
    skill: SkillSelection = "both",
    split: Split = "test",
    live: bool = False,
    max_cases: int = 100,
) -> dict:
    raw = await asyncio.to_thread(dataset_path.read_bytes)
    dataset = SkillDataset.model_validate_json(raw)
    gates = Gates.model_validate_json(await asyncio.to_thread(gates_path.read_bytes))
    cases = [case for case in dataset.cases if case.split == split]
    if not cases or len(cases) > max_cases:
        raise ValueError("Skill case budget exceeded or selected split is empty")
    if live and inference.capabilities.model_id.startswith("offline-"):
        raise ValueError("Live skill qualification requires explicitly configured models")
    reports = {}
    for selected in ("labeling", "extraction"):
        if skill not in (selected, "both"):
            continue
        labels: list[ContentLabeling] = []
        extractions: list[Extraction] = []
        rows = []
        usages: list[Usage] = []
        metered = True
        errors = 0
        latencies = []
        for case in cases:
            started = time.monotonic()
            try:
                if selected == "labeling":
                    result = await inference.label(case.segment())
                    validate_labeling(result.labeling, case.segment())
                    labels.append(result.labeling)
                    requires_model = True
                else:
                    # Gold routing isolates extractor quality from labeling mistakes.
                    result = await inference.extract_scoped(case.segment(), case.labeling)
                    validate_scoped_extraction(result.extraction, case.segment(), case.labeling)
                    extractions.append(result.extraction)
                    requires_model = any(
                        label.semantic_type
                        not in {
                            SemanticType.ARCHITECTURE_DOMAIN,
                            SemanticType.ORGANIZATIONAL_SCOPE,
                        }
                        for label in case.labeling.labels
                    )
                if requires_model:
                    usages.append(result.usage)
                    metered &= result.usage.input_tokens > 0 and result.usage.output_tokens > 0
                rows.append({"id": case.id, "result": result.model_dump(mode="json")})
            except Exception as error:
                errors += 1
                metered = False
                labels.append(ContentLabeling()) if selected == "labeling" else extractions.append(
                    Extraction()
                )
                rows.append({"id": case.id, "error": type(error).__name__})
            latencies.append((time.monotonic() - started) * 1000)
        metrics = (
            label_metrics(cases, labels)
            if selected == "labeling"
            else extraction_metrics(cases, extractions)
        )
        false_mandates = 0
        if selected == "extraction":
            legacy = [
                Case(
                    id=case.id,
                    source=case.family,
                    text=case.text,
                    expected=[
                        Expected(
                            semantic_type=c.semantic_type,
                            excerpt=c.excerpt,
                            modality=c.modality,
                            scope=c.scope.model_dump(mode="json", exclude_none=True),
                        )
                        for c in case.expected.candidates
                    ],
                )
                for case in cases
            ]
            false_mandates = score(legacy, extractions)["false_mandatory_requirements"]
        usage = total_usage(usages)
        p95 = sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1]
        safety = {"no_errors": errors == 0, "no_false_mandates": false_mandates == 0}
        quality = {
            "precision": (metrics["precision"] or 0) >= gates.minimum_precision,
            "recall": (metrics["recall"] or 0) >= gates.minimum_recall,
            "latency": p95 <= gates.maximum_p95_ms,
            "tokens": usage.input_tokens + usage.output_tokens <= gates.maximum_tokens,
            "metered_usage": metered,
            "priced_cost": usage.cost is not None and usage.cost <= gates.maximum_estimated_cost,
        }
        reports[selected] = {
            "fingerprint": inference.label_fingerprint
            if selected == "labeling"
            else inference.extraction_fingerprint,
            "metrics": metrics,
            "usage": usage.model_dump(mode="json"),
            "p95_ms": p95,
            "safety_gates": safety,
            "quality_gates": quality,
            "passed": all(safety.values()) and (not live or all(quality.values())),
            "qualified": live
            and split == "test"
            and all(safety.values())
            and all(quality.values()),
            "cases": rows,
        }
    report = {
        "dataset_version": dataset.version,
        "dataset_sha256": content_hash(raw),
        "review_status": dataset.review_status,
        "split": split,
        "live": live,
        "skills": reports,
        "passed": all(report["passed"] for report in reports.values()),
        "limitations": [
            "Provisional attributed/synthetic cases; not production model qualification.",
            "Extraction uses gold routing; combined ingestion also needs end-to-end evaluation.",
            "Offline safety success does not qualify a model or establish precision.",
        ],
    }
    await write_json(output, report)
    return report
