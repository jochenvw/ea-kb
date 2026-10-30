from __future__ import annotations

import asyncio
import math
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

import pymupdf
from pydantic import Field, model_validator
from pydantic_evals import Case as EvalCase
from pydantic_evals import Dataset as EvalDataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from ekb.domain.models import (
    ContentLabeling,
    Extraction,
    Model,
    Segment,
    SemanticType,
    Usage,
    content_hash,
    fingerprint,
    stable_id,
    total_usage,
)
from ekb.domain.skills import validate_labeling, validate_scoped_extraction
from ekb.evaluation import Case, Expected, qualifier_preserved, ratio, score
from ekb.evaluation.dataset import Source, Split, write_json
from ekb.evaluation.frugal import Gates
from ekb.evaluation.history import HISTORY, persist_run
from ekb.ports import IngestionSkills

type SkillSelection = Literal["labeling", "extraction", "both"]


class DocumentPassage(Model):
    excerpt: str = Field(min_length=1)
    section: str
    pdf_page: int | None = Field(default=None, gt=0)


class DocumentSample(Model):
    kind: Literal["annual_report", "framework", "metadata_only"]
    retained_path: Path
    hash_mode: Literal["bytes", "lf_normalized_text"] = "bytes"
    passages: tuple[DocumentPassage, ...] = Field(min_length=1)
    note: str

    @model_validator(mode="after")
    def local_path(self) -> Self:
        if self.retained_path.is_absolute() or ".." in self.retained_path.parts:
            raise ValueError("Document samples require repository-relative retained paths")
        if self.retained_path.suffix.casefold() == ".pdf" and self.hash_mode != "bytes":
            raise ValueError("PDF identity must preserve original bytes")
        return self


class SkillCase(Model):
    id: str
    family: str
    split: Split
    text: str
    labeling: ContentLabeling
    expected: Extraction
    source: Source | None = None
    document: DocumentSample | None = None

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
            if case.document:
                if case.source is None:
                    raise ValueError("Document samples require attributed source identity")
                if case.text != "\n\n".join(p.excerpt for p in case.document.passages):
                    raise ValueError(
                        "Document input must contain only its selected source passages"
                    )
                evidence = [
                    *(label.excerpt for label in case.labeling.labels),
                    *(candidate.excerpt for candidate in case.expected.candidates),
                    *([case.labeling.coverage.excerpt] if case.labeling.coverage else []),
                ]
                if any(
                    not any(excerpt in p.excerpt for p in case.document.passages)
                    for excerpt in evidence
                ):
                    raise ValueError("Document labels must resolve to individual source passages")
            for key in keys:
                if key in splits and splits[key] != case.split:
                    raise ValueError("Document families and duplicate text cannot cross splits")
                splits[key] = case.split
            validate_scoped_extraction(case.expected, case.segment(), case.labeling)
        if {case.split for case in self.cases} != {"train", "validation", "test"}:
            raise ValueError("Skill dataset requires train, validation and test cases")
        return self


class SkillExecution(Model):
    labeling: ContentLabeling | None = None
    extraction: Extraction | None = None
    usage: Usage = Field(default_factory=Usage)
    model_id: str | None = None
    prompt_version: str | None = None
    requires_model: bool = False
    error: str | None = None


@dataclass
class SkillQualityEvaluator(Evaluator[str, SkillExecution, SkillCase]):
    skill: Literal["labeling", "extraction"]

    def evaluate(
        self, ctx: EvaluatorContext[str, SkillExecution, SkillCase]
    ) -> dict[str, bool | int]:
        if ctx.metadata is None or ctx.output.error:
            return {"case_exact_match": False, "tp": 0, "fp": 0, "fn": 0}
        case = ctx.metadata
        if self.skill == "labeling":
            prediction = ctx.output.labeling or ContentLabeling()
            counts = label_metrics([case], [prediction])
        else:
            prediction = ctx.output.extraction or Extraction()
            counts = extraction_metrics([case], [prediction])
        return {
            "case_exact_match": counts["fp"] == 0 and counts["fn"] == 0,
            "tp": counts["tp"],
            "fp": counts["fp"],
            "fn": counts["fn"],
        }


class ModelCallBudget:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.reserved = 0
        self._lock = asyncio.Lock()

    async def reserve(self, calls: int) -> bool:
        async with self._lock:
            if self.reserved + calls > self.maximum:
                return False
            self.reserved += calls
            return True


@dataclass
class SkillRunState:
    active: int = 0
    maximum_active: int = 0


async def _run_skill_case(
    case_id: str,
    *,
    cases: dict[str, SkillCase],
    selected: Literal["labeling", "extraction"],
    inference: IngestionSkills,
    live: bool,
    calls_per_case: int,
    call_budget: ModelCallBudget,
    state: SkillRunState,
    completed: dict[str, SkillExecution],
) -> SkillExecution:
    case = cases[case_id]
    requires_model = selected == "labeling" or any(
        label.semantic_type
        not in {SemanticType.ARCHITECTURE_DOMAIN, SemanticType.ORGANIZATIONAL_SCOPE}
        for label in case.labeling.labels
    )
    state.active += 1
    state.maximum_active = max(state.maximum_active, state.active)
    try:
        if live and requires_model and not await call_budget.reserve(calls_per_case):
            result = SkillExecution(requires_model=True, error="model_call_budget_exceeded")
        elif selected == "labeling":
            response = await inference.label(case.segment())
            validate_labeling(response.labeling, case.segment())
            result = SkillExecution(
                labeling=response.labeling,
                usage=response.usage,
                model_id=response.model_id,
                prompt_version=response.prompt_version,
                requires_model=requires_model,
            )
        else:
            response = await inference.extract_scoped(case.segment(), case.labeling)
            validate_scoped_extraction(response.extraction, case.segment(), case.labeling)
            result = SkillExecution(
                extraction=response.extraction,
                usage=response.usage,
                model_id=response.model_id,
                prompt_version=response.prompt_version,
                requires_model=requires_model,
            )
    except Exception as error:
        result = SkillExecution(requires_model=requires_model, error=type(error).__name__)
    finally:
        state.active -= 1
    completed[case.id] = result
    return result


def _serialize_evaluator_results(results: dict) -> dict[str, dict[str, Any]]:
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


def _audit_document_sources(cases: list[SkillCase]) -> dict:
    checked = 0
    for case in cases:
        if case.document is None or case.source is None:
            raise ValueError("Source audit requires document samples with source identity")
        path = case.document.retained_path
        try:
            raw = path.read_bytes()
        except OSError:
            raise ValueError(f"Retained document unavailable: {case.id}") from None
        identity = (
            raw.decode("utf-8-sig").replace("\r\n", "\n").encode()
            if case.document.hash_mode == "lf_normalized_text"
            else raw
        )
        if content_hash(identity) != case.source.sha256:
            raise ValueError(f"Retained document hash changed: {case.id}")
        if path.suffix.casefold() == ".pdf":
            with pymupdf.open(stream=raw, filetype="pdf") as document:
                for passage in case.document.passages:
                    if passage.pdf_page is None or passage.pdf_page > len(document):
                        raise ValueError(f"Invalid source PDF page: {case.id}")
                    page = document[passage.pdf_page - 1]
                    texts = (page.get_text("text", sort=False), page.get_text("text", sort=True))
                    normalized = " ".join(passage.excerpt.split())
                    if not any(
                        isinstance(text, str) and normalized in " ".join(text.split())
                        for text in texts
                    ):
                        raise ValueError(f"Source passage not found on cited page: {case.id}")
                    checked += 1
        else:
            text = " ".join(raw.decode("utf-8-sig").split())
            for passage in case.document.passages:
                if passage.pdf_page is not None or " ".join(passage.excerpt.split()) not in text:
                    raise ValueError(f"Source text passage not found: {case.id}")
                checked += 1
    return {"status": "passed", "documents": len(cases), "passages": checked}


def _scope_summaries(labeling: ContentLabeling) -> list[dict]:
    scopes = {
        label.scope.model_dump_json(exclude_none=True): label.scope.model_dump(
            mode="json", exclude_none=True
        )
        for label in labeling.labels
        if label.scope.model_dump(exclude_none=True)
    }
    return [scopes[key] for key in sorted(scopes)]


def document_metrics(cases: list[SkillCase], predictions: list[ContentLabeling]) -> dict:
    rows = []
    coverage_correct = coverage_count = expected_count = actual_count = matched = 0
    for case, prediction in zip(cases, predictions, strict=True):
        if case.document is None:
            continue
        expected_types = {
            label.semantic_type for label in case.labeling.labels if label.semantic_type
        }
        actual_types = {label.semantic_type for label in prediction.labels if label.semantic_type}
        expected_count += len(expected_types)
        actual_count += len(actual_types)
        matched += len(expected_types & actual_types)
        expected_coverage = case.labeling.coverage.level if case.labeling.coverage else None
        actual_coverage = prediction.coverage.level if prediction.coverage else None
        if expected_coverage is not None:
            coverage_count += 1
            coverage_correct += expected_coverage == actual_coverage
        rows.append(
            {
                "id": case.id,
                "kind": case.document.kind,
                "source": case.source.model_dump(mode="json") if case.source else None,
                "source_note": case.document.note,
                "expected_coverage": expected_coverage,
                "actual_coverage": actual_coverage,
                "expected_ontology_types": sorted(expected_types),
                "actual_ontology_types": sorted(actual_types),
                "ontology_types_exact_match": expected_types == actual_types,
                "missing_ontology_types": sorted(expected_types - actual_types),
                "unexpected_ontology_types": sorted(actual_types - expected_types),
                "expected_scopes": _scope_summaries(case.labeling),
                "actual_scopes": _scope_summaries(prediction),
                "unclassified_labels": sum(
                    label.semantic_type is None for label in prediction.labels
                ),
            }
        )
    return {
        "coverage": {
            "correct": coverage_correct,
            "scored": coverage_count,
            "accuracy": ratio(coverage_correct, coverage_count),
        },
        "ontology_type_coverage": _counts(expected_count, actual_count, matched),
        "ontology_type_set_accuracy": ratio(
            sum(row["ontology_types_exact_match"] for row in rows), len(rows)
        ),
        "documents": rows,
        "limitation": "Selected passages, not exhaustive document coverage or adoption review.",
    }


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
    split: Split | Literal["all"] = "test",
    live: bool = False,
    max_cases: int = 100,
    max_concurrency: int = 8,
    max_model_calls: int = 10_000,
    calls_per_case: int = 6,
    verify_sources: bool = False,
    run_metadata: dict[str, Any] | None = None,
    history_dir: Path = HISTORY,
) -> dict:
    raw = await asyncio.to_thread(dataset_path.read_bytes)
    dataset = SkillDataset.model_validate_json(raw)
    gates = Gates.model_validate_json(await asyncio.to_thread(gates_path.read_bytes))
    cases = [case for case in dataset.cases if split == "all" or case.split == split]
    if not 1 <= max_cases <= 1000 or not cases or len(cases) > max_cases:
        raise ValueError("Skill case budget exceeded or selected split is empty")
    if not 1 <= max_concurrency <= 32 or not 1 <= max_model_calls <= 100_000 or calls_per_case < 1:
        raise ValueError(
            "Evaluation concurrency and model-call budgets are outside supported limits"
        )
    if live and inference.capabilities.model_id.startswith("offline-"):
        raise ValueError("Live skill qualification requires explicitly configured models")
    source_audit = (
        await asyncio.to_thread(_audit_document_sources, cases)
        if verify_sources
        else {"status": "not_requested"}
    )
    reports = {}
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    call_budget = ModelCallBudget(max_model_calls)
    cancelled = False
    failure_details: dict[str, str] = {}
    for selected in ("labeling", "extraction"):
        if skill not in (selected, "both"):
            continue
        by_id = {case.id: case for case in cases}
        completed: dict[str, SkillExecution] = {}
        state = SkillRunState()

        eval_dataset = EvalDataset[str, SkillExecution, SkillCase](
            name=f"{selected}-{dataset.version}-{split}",
            cases=[
                EvalCase(
                    name=case.id,
                    inputs=case.id,
                    metadata=case,
                    evaluators=[SkillQualityEvaluator(selected)],
                )
                for case in cases
            ],
        )
        framework_report = None
        framework_error = None
        started = time.monotonic()
        try:
            framework_report = await eval_dataset.evaluate(
                partial(
                    _run_skill_case,
                    cases=by_id,
                    selected=selected,
                    inference=inference,
                    live=live,
                    calls_per_case=calls_per_case,
                    call_budget=call_budget,
                    state=state,
                    completed=completed,
                ),
                max_concurrency=max_concurrency,
                progress=False,
                task_name=f"ekb-{selected}",
                metadata={
                    "dataset_version": dataset.version,
                    "dataset_sha256": content_hash(raw),
                    "skill_fingerprint": inference.label_fingerprint
                    if selected == "labeling"
                    else inference.extraction_fingerprint,
                    "split": split,
                },
            )
        except asyncio.CancelledError:
            cancelled = True
        except Exception as error:
            framework_error = type(error).__name__
            failure_details["evaluation_runner"] = framework_error
        wall_ms = (time.monotonic() - started) * 1000
        framework_cases = (
            {case.name: case for case in framework_report.cases} if framework_report else {}
        )
        if framework_report:
            for name in framework_report.failures:
                failure_details[f"{selected}:{name}"] = "evaluation_case_failed"
        executions = [
            completed.get(case.id)
            or SkillExecution(
                requires_model=selected == "labeling",
                error="cancelled" if cancelled else "evaluation_runner_failed",
            )
            for case in cases
        ]
        if cancelled:
            for case, execution in zip(cases, executions, strict=True):
                completed.setdefault(case.id, execution)
        for case, execution in zip(cases, executions, strict=True):
            if execution.error:
                failure_details[f"{selected}:{case.id}"] = execution.error
        labels = [result.labeling or ContentLabeling() for result in executions]
        extractions = [result.extraction or Extraction() for result in executions]
        errors = sum(result.error is not None for result in executions)
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
        usages = [
            result.usage for result in executions if result.requires_model and result.error is None
        ]
        usage = total_usage(usages)
        latencies = [
            framework_cases[case.id].task_duration * 1000
            for case in cases
            if case.id in framework_cases
        ]
        latencies.sort()
        p50 = latencies[math.ceil(len(latencies) * 0.50) - 1] if latencies else None
        p95 = latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None
        metered = all(
            not result.requires_model
            or result.error is not None
            or (
                result.usage.input_tokens > 0
                and result.usage.output_tokens > 0
                and result.usage.model_calls > 0
            )
            for result in executions
        )
        safety = {
            "no_errors": errors == 0 and framework_error is None and not cancelled,
            "no_false_mandates": false_mandates == 0,
            "within_model_call_budget": usage.model_calls <= max_model_calls,
        }
        quality = {
            "precision": (metrics["precision"] or 0) >= gates.minimum_precision,
            "recall": (metrics["recall"] or 0) >= gates.minimum_recall,
            "latency": p95 is not None and p95 <= gates.maximum_p95_ms,
            "tokens": usage.input_tokens + usage.output_tokens <= gates.maximum_tokens,
            "metered_usage": metered,
            "priced_cost": usage.cost is not None and usage.cost <= gates.maximum_estimated_cost,
        }
        documents = (
            document_metrics(cases, labels)
            if selected == "labeling" and any(case.document for case in cases)
            else None
        )
        if documents:
            quality["document_coverage"] = documents["coverage"]["accuracy"] == 1
        reports[selected] = {
            "fingerprint": inference.label_fingerprint
            if selected == "labeling"
            else inference.extraction_fingerprint,
            "metrics": metrics,
            "usage": usage.model_dump(mode="json"),
            "errors": errors,
            "actual_model_ids": sorted(
                {result.model_id for result in executions if result.model_id is not None}
            ),
            "p95_ms": p95,
            "performance": {
                "p50_case_latency_ms": p50,
                "p95_case_latency_ms": p95,
                "wall_time_ms": wall_ms,
                "cases_per_second": (len(framework_cases) / (wall_ms / 1000) if wall_ms else None),
                "completed_cases": sum(result.error is None for result in executions),
                "max_observed_concurrency": state.maximum_active,
                "configured_concurrency": max_concurrency,
                "measured_model_requests": usage.model_calls,
                "measured_input_tokens": usage.input_tokens,
                "measured_output_tokens": usage.output_tokens,
                "measured_cost": usage.cost,
                "retry_count": None,
                "throttle_count": None,
                "cache_hits": 0,
                "judge_requests": 0,
                "model_call_budget_reserved": call_budget.reserved,
                "model_call_budget_limit": max_model_calls,
                "usage_complete": metered,
            },
            "safety_gates": safety,
            "quality_gates": quality,
            "passed": all(safety.values()) and (not live or all(quality.values())),
            "qualified": live
            and split == "test"
            and all(safety.values())
            and all(quality.values()),
            "cases": [
                {
                    "id": case.id,
                    "result": execution.model_dump(mode="json"),
                    "duration_ms": (
                        framework_cases[case.id].task_duration * 1000
                        if case.id in framework_cases
                        else None
                    ),
                    "scores": (
                        _serialize_evaluator_results(framework_cases[case.id].scores)
                        if case.id in framework_cases
                        else {}
                    ),
                    "assertions": (
                        _serialize_evaluator_results(framework_cases[case.id].assertions)
                        if case.id in framework_cases
                        else {}
                    ),
                }
                for case, execution in zip(cases, executions, strict=True)
            ],
            "evaluation_runner": "pydantic-evals",
            "evaluation_runner_version": version("pydantic-evals"),
        }
        if framework_error:
            reports[selected]["runner_error"] = framework_error
        if selected == "extraction":
            empty_cases = [
                (case, prediction)
                for case, prediction in zip(cases, extractions, strict=True)
                if not case.expected.candidates
            ]
            reports[selected]["negative_cases"] = {
                "scored": len(empty_cases),
                "correctly_empty": sum(not prediction.candidates for _, prediction in empty_cases),
            }
        if documents:
            reports[selected]["document_classification"] = documents
        if cancelled:
            break
    report = {
        "schema_version": "skill-evaluation-v2",
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "cancelled" if cancelled else "partial" if failure_details else "completed",
        "dataset_path": str(dataset_path),
        "dataset_version": dataset.version,
        "model_id": inference.capabilities.model_id,
        "dataset_sha256": content_hash(raw),
        "code_revision": await asyncio.to_thread(_git_revision),
        "configuration_fingerprint": fingerprint(
            model_id=inference.capabilities.model_id,
            label_fingerprint=inference.label_fingerprint,
            extraction_fingerprint=inference.extraction_fingerprint,
            run_configuration=run_metadata or {},
        ),
        "run_configuration": {
            **(run_metadata or {}),
            "max_concurrency": max_concurrency,
            "max_model_calls": max_model_calls,
            "calls_per_case_reserve": calls_per_case,
            "case_retries": "provider-managed; Pydantic Evals retries disabled",
        },
        "failure_details": failure_details,
        "errors": len(failure_details),
        "review_status": dataset.review_status,
        "split": split,
        "live": live,
        "source_audit": source_audit,
        "skills": reports,
        "passed": not cancelled
        and not failure_details
        and all(report["passed"] for report in reports.values()),
        "limitations": [
            "Provisional attributed/synthetic cases; not production model qualification.",
            "Extraction uses gold routing; combined ingestion also needs end-to-end evaluation.",
            "Offline safety success does not qualify a model or establish precision.",
        ],
    }
    report["history_path"] = str(await persist_run(report, history=history_dir))
    await write_json(output, report)
    if cancelled:
        raise asyncio.CancelledError
    return report


async def compare_skill_concurrency(
    dataset_path: Path,
    gates_path: Path,
    output: Path,
    inference: IngestionSkills,
    *,
    skill: SkillSelection,
    split: Split,
    max_cases: int,
    max_concurrency: int,
    max_model_calls: int,
    calls_per_case: int,
    run_metadata: dict[str, Any],
    history_dir: Path = HISTORY,
) -> dict[str, Any]:
    if not inference.capabilities.model_id or inference.capabilities.model_id.startswith(
        "offline-"
    ):
        raise ValueError("Concurrency comparisons require an explicitly configured live model")
    if max_concurrency < 2 or max_model_calls < 2:
        raise ValueError("Comparison requires concurrency >= 2 and a budget for both runs")
    sequential_path = output.with_name(f"{output.stem}-sequential{output.suffix}")
    parallel_path = output.with_name(f"{output.stem}-parallel{output.suffix}")
    run_budget = max_model_calls // 2
    sequential = await evaluate_skills(
        dataset_path,
        gates_path,
        sequential_path,
        inference,
        skill=skill,
        split=split,
        live=True,
        max_cases=max_cases,
        max_concurrency=1,
        max_model_calls=run_budget,
        calls_per_case=calls_per_case,
        run_metadata={**run_metadata, "comparison_arm": "sequential"},
        history_dir=history_dir,
    )
    parallel = await evaluate_skills(
        dataset_path,
        gates_path,
        parallel_path,
        inference,
        skill=skill,
        split=split,
        live=True,
        max_cases=max_cases,
        max_concurrency=max_concurrency,
        max_model_calls=max_model_calls - run_budget,
        calls_per_case=calls_per_case,
        run_metadata={**run_metadata, "comparison_arm": "bounded_parallel"},
        history_dir=history_dir,
    )
    parity = {
        name: {
            "same_quality_metrics": sequential["skills"][name]["metrics"]
            == parallel["skills"][name]["metrics"],
            "sequential_metrics": sequential["skills"][name]["metrics"],
            "parallel_metrics": parallel["skills"][name]["metrics"],
            "sequential_gates": sequential["skills"][name]["quality_gates"],
            "parallel_gates": parallel["skills"][name]["quality_gates"],
            "sequential_performance": sequential["skills"][name]["performance"],
            "parallel_performance": parallel["skills"][name]["performance"],
        }
        for name in sequential["skills"]
    }
    compatible = (
        sequential["dataset_sha256"] == parallel["dataset_sha256"]
        and sequential["split"] == parallel["split"]
        and sequential["model_id"] == parallel["model_id"]
        and all(
            report["performance"]["cache_hits"] == 0
            for evaluation in (sequential, parallel)
            for report in evaluation["skills"].values()
        )
    )
    seq_wall = sum(item["performance"]["wall_time_ms"] for item in sequential["skills"].values())
    parallel_wall = sum(item["performance"]["wall_time_ms"] for item in parallel["skills"].values())
    costs = [
        item["usage"]["cost"]
        for evaluation in (sequential, parallel)
        for item in evaluation["skills"].values()
    ]
    report = {
        "schema_version": "skill-concurrency-comparison-v1",
        "run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8],
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "completed" if compatible else "incomparable",
        "dataset_path": str(dataset_path),
        "dataset_sha256": sequential["dataset_sha256"],
        "split": split,
        "model_id": sequential["model_id"],
        "live": True,
        "cache_policy": "disabled; no skill-evaluation cache",
        "max_concurrency": max_concurrency,
        "sequential_run_id": sequential["run_id"],
        "parallel_run_id": parallel["run_id"],
        "compatible": compatible,
        "quality_parity": parity,
        "aggregate": {
            "sequential_wall_time_ms": seq_wall,
            "parallel_wall_time_ms": parallel_wall,
            "speedup": seq_wall / parallel_wall if parallel_wall else None,
            "sequential_model_requests": sum(
                item["performance"]["measured_model_requests"]
                for item in sequential["skills"].values()
            ),
            "parallel_model_requests": sum(
                item["performance"]["measured_model_requests"]
                for item in parallel["skills"].values()
            ),
            "combined_measured_cost": (
                sum(costs) if all(cost is not None for cost in costs) else None
            ),
        },
        "passed": compatible
        and sequential["passed"]
        and parallel["passed"]
        and all(item["same_quality_metrics"] for item in parity.values()),
    }
    report["history_path"] = str(
        await persist_run(report, history=history_dir, prefix="skill-comparison")
    )
    await write_json(output, report)
    return report


def _git_revision() -> dict[str, str | bool | None]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, check=True, text=True, timeout=3
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                capture_output=True,
                check=True,
                text=True,
                timeout=3,
            ).stdout.strip()
        )
        return {"revision": revision, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"revision": None, "dirty": None}
