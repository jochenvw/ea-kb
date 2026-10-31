from __future__ import annotations

import asyncio
import inspect
import math
import subprocess
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlsplit
from uuid import uuid4

import pymupdf
from pydantic import Field, model_validator
from pydantic_evals import Case as EvalCase
from pydantic_evals import Dataset as EvalDataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from ekb.config import Settings
from ekb.domain.grounding import validate_evidence
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
from ekb.evaluation.dataset import Source, Split
from ekb.evaluation.frugal import Gates
from ekb.evaluation.history import HISTORY, persist_run, validate_output_path, write_latest_report
from ekb.evaluation.limits import DeploymentRateLimiter
from ekb.ports import IngestionSkills, RequestControlledSkills

type SkillSelection = Literal["labeling", "extraction", "both"]


def skill_run_metadata(settings: Settings | None) -> dict[str, Any]:
    if settings is None:
        return {"provider": "offline-rules", "model_version": None}
    return {
        "provider": settings.inference,
        "endpoint_host": urlsplit(settings.model_endpoint).hostname,
        "deployment": settings.model_id,
        "labeling_deployment": settings.labeling_model_id or settings.model_id,
        "model_version": None,
        "temperature": settings.model_temperature,
        "retries": settings.model_retries,
        "structured_output": settings.model_structured_output,
        "context_tokens": settings.model_context_tokens,
        "max_output_tokens": settings.model_max_output_tokens,
        "max_token_parameter": settings.model_max_token_parameter,
        "input_price": settings.model_input_price,
        "output_price": settings.model_output_price,
        "labeling_input_price": settings.labeling_input_price,
        "labeling_output_price": settings.labeling_output_price,
    }


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
    heading: str | None = None
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
            heading=self.heading,
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
    provider_model_ids: tuple[str, ...] = ()
    provider_model_identity_complete: bool | None = None
    prompt_version: str | None = None
    requires_model: bool = False
    error: str | None = None


@dataclass
class SkillQualityEvaluator(Evaluator[str, SkillExecution, SkillCase]):
    skill: Literal["labeling", "extraction"]

    def get_evaluator_version(self) -> str:
        return "skill-quality-v2"

    def evaluate(
        self, ctx: EvaluatorContext[str, SkillExecution, SkillCase]
    ) -> dict[str, bool | int]:
        if ctx.metadata is None:
            return {"case_exact_match": False, "tp": 0, "fp": 0, "fn": 0}
        case = ctx.metadata
        if self.skill == "labeling":
            prediction = ctx.output.labeling or ContentLabeling()
            counts = label_metrics([case], [prediction])
        else:
            prediction = ctx.output.extraction or Extraction()
            counts = extraction_metrics([case], [prediction])
        return {
            "case_exact_match": not ctx.output.error and counts["fp"] == 0 and counts["fn"] == 0,
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
    started: set[str] = field(default_factory=set)


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
        label.semantic_type is not SemanticType.ORGANIZATIONAL_SCOPE
        for label in case.labeling.labels
    )
    state.active += 1
    state.maximum_active = max(state.maximum_active, state.active)
    response_usage = Usage()
    response_model_id: str | None = None
    response_provider_model_ids: tuple[str, ...] = ()
    response_provider_identity_complete: bool | None = None
    response_prompt_version: str | None = None
    try:
        if live and not await call_budget.reserve(calls_per_case):
            result = SkillExecution(
                requires_model=requires_model, error="model_call_budget_exceeded"
            )
        else:
            state.started.add(case.id)
            if selected == "labeling":
                response = await inference.label(case.segment())
                response_usage = response.usage
                response_model_id = response.model_id
                response_provider_model_ids = response.provider_model_ids
                response_provider_identity_complete = response.provider_model_identity_complete
                response_prompt_version = response.prompt_version
                validate_labeling(response.labeling, case.segment())
                result = SkillExecution(
                    labeling=response.labeling,
                    usage=response.usage,
                    model_id=response.model_id,
                    provider_model_ids=response.provider_model_ids,
                    provider_model_identity_complete=response.provider_model_identity_complete,
                    prompt_version=response.prompt_version,
                    requires_model=requires_model or response.usage.model_calls > 0,
                )
            else:
                response = await inference.extract_scoped(case.segment(), case.labeling)
                response_usage = response.usage
                response_model_id = response.model_id
                response_provider_model_ids = response.provider_model_ids
                response_provider_identity_complete = response.provider_model_identity_complete
                response_prompt_version = response.prompt_version
                validate_scoped_extraction(response.extraction, case.segment(), case.labeling)
                result = SkillExecution(
                    extraction=response.extraction,
                    usage=response.usage,
                    model_id=response.model_id,
                    provider_model_ids=response.provider_model_ids,
                    provider_model_identity_complete=response.provider_model_identity_complete,
                    prompt_version=response.prompt_version,
                    requires_model=requires_model or response.usage.model_calls > 0,
                )
            if live and result.usage.model_calls > calls_per_case:
                result = result.model_copy(update={"error": "model_call_reservation_exceeded"})
    except Exception as error:
        result = SkillExecution(
            requires_model=requires_model or (live and case.id in state.started),
            usage=response_usage,
            model_id=response_model_id,
            provider_model_ids=response_provider_model_ids,
            provider_model_identity_complete=response_provider_identity_complete,
            prompt_version=response_prompt_version,
            error=type(error).__name__,
        )
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


def _evaluator_fingerprint(skill: Literal["labeling", "extraction"], gates_raw: bytes) -> str:
    definitions = [
        SkillQualityEvaluator,
        SkillDataset,
        _counts,
        ratio,
        label_metrics if skill == "labeling" else extraction_metrics,
        validate_labeling if skill == "labeling" else validate_scoped_extraction,
    ]
    if skill == "extraction":
        definitions.extend((score, qualifier_preserved, validate_labeling, validate_evidence))
    else:
        definitions.extend((document_metrics, _scope_summaries))
    return fingerprint(
        skill=skill,
        evaluator_version=SkillQualityEvaluator(skill).get_evaluator_version(),
        runner_version=version("pydantic-evals"),
        definitions=[inspect.getsource(definition) for definition in definitions],
        labeling_schema=ContentLabeling.model_json_schema(),
        extraction_schema=Extraction.model_json_schema(),
        gates_sha256=content_hash(gates_raw),
    )


def _call_reservation(inference: IngestionSkills, calls: int | None, *, live: bool) -> int:
    provider_limit: int | None = getattr(inference, "max_requests_per_skill", None)
    if calls is None:
        if provider_limit is None:
            if live:
                raise ValueError("Live evaluation requires a declared per-case model-call bound")
            return 1
        calls = max(provider_limit, 1)
    if calls < 1 or (live and provider_limit is not None and calls < provider_limit):
        raise ValueError("Evaluation model-call reservation is below the provider's bound")
    return calls


def _validate_output(output: Path, dataset_path: Path, gates_path: Path) -> None:
    validate_output_path(output)
    if output.resolve() in {dataset_path.resolve(), gates_path.resolve()}:
        raise ValueError("A latest-report output cannot overwrite an evaluation input")


async def _publish_report(
    report: dict[str, Any], output: Path, history_dir: Path, *, prefix: str = "skill-eval"
) -> bool:
    async def save() -> None:
        await persist_run(report, history=history_dir, prefix=prefix)
        await write_latest_report(output, report)

    save_task = asyncio.create_task(save())
    cancelled = False
    while True:
        try:
            await asyncio.shield(save_task)
            return cancelled
        except asyncio.CancelledError:
            if save_task.cancelled():
                raise
            cancelled = True


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
    calls_per_case: int | None = None,
    verify_sources: bool = False,
    run_metadata: dict[str, Any] | None = None,
    history_dir: Path = HISTORY,
    rate_limiter: DeploymentRateLimiter | None = None,
    _run_id: str | None = None,
) -> dict:
    async def prepare():
        await asyncio.to_thread(_validate_output, output, dataset_path, gates_path)
        if skill not in {"labeling", "extraction", "both"}:
            raise ValueError("Skill must be labeling, extraction, or both")
        raw = await asyncio.to_thread(dataset_path.read_bytes)
        dataset = SkillDataset.model_validate_json(raw)
        gates_raw = await asyncio.to_thread(gates_path.read_bytes)
        gates = Gates.model_validate_json(gates_raw)
        cases = [case for case in dataset.cases if split == "all" or case.split == split]
        if not 1 <= max_cases <= 1000 or not cases or len(cases) > max_cases:
            raise ValueError("Skill case budget exceeded or selected split is empty")
        reservation = _call_reservation(inference, calls_per_case, live=live)
        if not 1 <= max_concurrency <= 32 or not 1 <= max_model_calls <= 100_000:
            raise ValueError(
                "Evaluation concurrency and model-call budgets are outside supported limits"
            )
        if rate_limiter is not None:
            if not isinstance(inference, RequestControlledSkills):
                raise ValueError("Rate-limited evaluation requires per-request provider control")
            rate_limiter.check_capacity(1)
        if live and inference.capabilities.model_id.startswith("offline-"):
            raise ValueError("Live skill qualification requires explicitly configured models")
        source_audit = (
            await asyncio.to_thread(_audit_document_sources, cases)
            if verify_sources
            else {"status": "not_requested"}
        )
        revision = await asyncio.to_thread(_git_revision)
        return raw, dataset, gates_raw, gates, cases, reservation, source_audit, revision

    run_id = _run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    started_at = datetime.now(UTC).isoformat()
    metadata = run_metadata or {}
    preparation = asyncio.create_task(prepare())
    cancelled = False
    while True:
        try:
            (
                raw,
                dataset,
                gates_raw,
                gates,
                cases,
                calls_per_case,
                source_audit,
                code_revision,
            ) = await asyncio.shield(preparation)
            break
        except asyncio.CancelledError:
            if preparation.cancelled():
                raise
            cancelled = True
    reports = {}
    call_budget = ModelCallBudget(max_model_calls)
    failure_details: dict[str, str] = {}
    for selected in ("labeling", "extraction"):
        if skill not in (selected, "both"):
            continue
        by_id = {case.id: case for case in cases}
        completed: dict[str, SkillExecution] = {}
        state = SkillRunState()
        deployment = str(
            metadata.get("labeling_deployment" if selected == "labeling" else "deployment")
            or inference.capabilities.model_id
        )
        rate_before = rate_limiter.requests_reserved() if rate_limiter else {}
        evaluator_fingerprint = _evaluator_fingerprint(selected, gates_raw)
        framework_cases: dict[str, Any] = {}
        framework_failures: set[str] = set()
        framework_error = None
        started = time.monotonic()
        try:
            # Pydantic Evals creates a task for each submitted case; bound each submission too.
            batch_size = max_concurrency * 2
            for offset in range(0, 0 if cancelled else len(cases), batch_size):
                eval_dataset = EvalDataset[str, SkillExecution, SkillCase](
                    name=f"{selected}-{dataset.version}-{split}",
                    cases=[
                        EvalCase(
                            name=case.id,
                            inputs=case.id,
                            metadata=case,
                            evaluators=[SkillQualityEvaluator(selected)],
                        )
                        for case in cases[offset : offset + batch_size]
                    ],
                )
                request_scope = (
                    inference.limit_model_requests(rate_limiter)
                    if rate_limiter and isinstance(inference, RequestControlledSkills)
                    else nullcontext()
                )
                with request_scope:
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
                            "evaluator_fingerprint": evaluator_fingerprint,
                            "split": split,
                        },
                    )
                framework_cases.update({case.name: case for case in framework_report.cases})
                framework_failures.update(failure.name for failure in framework_report.failures)
                framework_failures.update(
                    case.name for case in framework_report.cases if case.evaluator_failures
                )
        except asyncio.CancelledError:
            cancelled = True
        except Exception as error:
            framework_error = type(error).__name__
            failure_details["evaluation_runner"] = framework_error
        wall_ms = (time.monotonic() - started) * 1000
        executions = [
            (
                completed[case.id].model_copy(update={"error": "evaluation_case_failed"})
                if case.id in framework_failures and case.id in completed
                else completed.get(case.id)
            )
            or SkillExecution(
                requires_model=case.id in state.started,
                error=(
                    "cancelled_after_start"
                    if cancelled and case.id in state.started
                    else "cancelled_before_start"
                    if cancelled
                    else "evaluation_case_failed"
                    if case.id in framework_failures
                    else "evaluation_runner_failed"
                ),
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
            result.usage
            for case, result in zip(cases, executions, strict=True)
            if case.id in state.started and (result.requires_model or result.usage.model_calls > 0)
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
        requests_complete = all(
            case.id not in state.started
            or (
                result.error is None
                and (
                    (not live and inference.capabilities.model_id.startswith("offline-"))
                    or not result.requires_model
                    or result.usage.model_calls > 0
                )
            )
            for case, result in zip(cases, executions, strict=True)
        )
        if not requests_complete:
            usage = usage.model_copy(
                update={
                    "cost": None,
                    "unmetered_model_calls": None,
                    "retry_count": None,
                    "throttles": None,
                    "cached_input_tokens": None,
                }
            )
        metered = (
            requests_complete
            and usage.unmetered_model_calls == 0
            and all(
                case.id not in state.started
                or (
                    result.error is None
                    and (
                        (not live and inference.capabilities.model_id.startswith("offline-"))
                        or (not result.requires_model and result.usage.model_calls == 0)
                        or (
                            result.usage.input_tokens > 0
                            and result.usage.output_tokens > 0
                            and result.usage.model_calls > 0
                        )
                    )
                )
                for case, result in zip(cases, executions, strict=True)
            )
        )
        rate_by_deployment = (
            {
                name: count - rate_before.get(name, 0)
                for name, count in rate_limiter.requests_reserved().items()
                if count > rate_before.get(name, 0)
            }
            if rate_limiter
            else None
        )
        rate_requests = sum(rate_by_deployment.values()) if rate_by_deployment is not None else None
        safety = {
            "no_errors": errors == 0 and framework_error is None and not cancelled,
            "no_false_mandates": false_mandates == 0,
            "within_model_call_budget": usage.model_calls <= max_model_calls
            and all(result.error != "model_call_reservation_exceeded" for result in executions),
        }
        quality = {
            "precision": (metrics["precision"] or 0) >= gates.minimum_precision,
            "recall": (metrics["recall"] or 0) >= gates.minimum_recall,
            "latency": p95 is not None and p95 <= gates.maximum_p95_ms,
            "tokens": metered and usage.input_tokens + usage.output_tokens <= gates.maximum_tokens,
            "metered_usage": metered,
            "priced_cost": metered
            and usage.cost is not None
            and usage.cost <= gates.maximum_estimated_cost,
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
            "evaluator_fingerprint": evaluator_fingerprint,
            "deployment": deployment,
            "rate_limited_requests_by_deployment": rate_by_deployment,
            "metrics": metrics,
            "usage": usage.model_dump(mode="json"),
            "errors": errors,
            "actual_model_ids": sorted(
                {model_id for result in executions for model_id in result.provider_model_ids}
            ),
            "reported_deployment_ids": sorted(
                {result.model_id for result in executions if result.model_id is not None}
            ),
            "prompt_versions": sorted(
                {
                    result.prompt_version
                    for result in executions
                    if result.prompt_version is not None
                }
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
                "measured_model_requests": usage.model_calls if requests_complete else None,
                "measured_input_tokens": usage.input_tokens if metered else None,
                "measured_output_tokens": usage.output_tokens if metered else None,
                "measured_cost": (usage.cost if usages else 0.0) if metered else None,
                "reported_model_requests": usage.model_calls,
                "reported_input_tokens": usage.input_tokens,
                "reported_output_tokens": usage.output_tokens,
                "retry_count": usage.retry_count if requests_complete else None,
                "throttle_count": usage.throttles if requests_complete else None,
                "cache_hits": 0,
                "provider_cache_hits": None,
                "provider_cached_input_tokens": (
                    usage.cached_input_tokens if requests_complete else None
                ),
                "judge_requests": 0,
                "model_call_budget_reserved": call_budget.reserved,
                "model_call_budget_limit": max_model_calls,
                "rate_limit_estimated_requests": rate_requests,
                "rate_limit_estimated_tokens": (
                    rate_requests * rate_limiter.estimated_tokens_per_request
                    if rate_limiter
                    and rate_requests is not None
                    and rate_limiter.estimated_tokens_per_request is not None
                    else None
                ),
                "requests_complete": requests_complete,
                "unmetered_model_requests": (
                    usage.unmetered_model_calls if requests_complete else None
                ),
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
    successful_cases = sum(item["performance"]["completed_cases"] for item in reports.values())
    cache_counts = [
        item["performance"]["provider_cached_input_tokens"] for item in reports.values()
    ]
    run_mode = (
        "offline"
        if not live
        else "live_provider_cache_unknown"
        if any(value is None for value in cache_counts)
        else "live_with_cached_input"
        if any(value > 0 for value in cache_counts)
        else "live_without_cached_input"
    )
    run_configuration = {
        **metadata,
        "max_concurrency": max_concurrency,
        "max_model_calls": max_model_calls,
        "calls_per_case_reserve": calls_per_case,
        "case_retries": "provider-managed; Pydantic Evals retries disabled",
        "rate_limits": rate_limiter.configuration() if rate_limiter else None,
        "cache_policy": "no evaluation-layer cache; provider cache status unknown",
    }
    report = {
        "schema_version": "skill-evaluation-v2",
        "run_id": run_id,
        "started_at": started_at,
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": (
            "cancelled"
            if cancelled
            else "failed"
            if failure_details and not successful_cases
            else "partial"
            if failure_details
            else "completed"
        ),
        "dataset_path": str(dataset_path),
        "dataset_version": dataset.version,
        "model_id": inference.capabilities.model_id,
        "model_version": metadata.get("model_version"),
        "provider": metadata.get("provider"),
        "dataset_sha256": content_hash(raw),
        "gates_sha256": content_hash(gates_raw),
        "code_revision": code_revision,
        "configuration_fingerprint": fingerprint(
            model_id=inference.capabilities.model_id,
            label_fingerprint=inference.label_fingerprint,
            extraction_fingerprint=inference.extraction_fingerprint,
            run_configuration=run_configuration,
        ),
        "run_configuration": run_configuration,
        "failure_details": failure_details,
        "errors": len(failure_details),
        "review_status": dataset.review_status,
        "split": split,
        "live": live,
        "run_mode": run_mode,
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
        "history_path": str(history_dir / f"skill-eval-{run_id}.json"),
    }

    save_cancelled = await _publish_report(report, output, history_dir)
    if cancelled or save_cancelled:
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
    calls_per_case: int | None,
    run_metadata: dict[str, Any],
    history_dir: Path = HISTORY,
    rate_limiter: DeploymentRateLimiter | None = None,
) -> dict[str, Any]:
    await asyncio.to_thread(_validate_output, output, dataset_path, gates_path)
    if not inference.capabilities.model_id or inference.capabilities.model_id.startswith(
        "offline-"
    ):
        raise ValueError("Concurrency comparisons require an explicitly configured live model")
    if not 2 <= max_concurrency <= 32 or not 2 <= max_model_calls <= 100_000:
        raise ValueError("Comparison requires concurrency >= 2 and a budget for both runs")
    if skill not in {"labeling", "extraction", "both"}:
        raise ValueError("Skill must be labeling, extraction, or both")
    calls_per_case = _call_reservation(inference, calls_per_case, live=True)
    dataset = SkillDataset.model_validate_json(await asyncio.to_thread(dataset_path.read_bytes))
    cases = [case for case in dataset.cases if case.split == split]
    if not 1 <= max_cases <= 1000 or not cases or len(cases) > max_cases:
        raise ValueError("Skill case budget exceeded or selected split is empty")
    required_per_arm = len(cases) * (2 if skill == "both" else 1) * calls_per_case
    run_budget = max_model_calls // 2
    if run_budget < required_per_arm:
        raise ValueError("Comparison budget cannot reserve equivalent work for both arms")
    if rate_limiter is not None:
        if not isinstance(inference, RequestControlledSkills):
            raise ValueError("Rate-limited evaluation requires per-request provider control")
        rate_limiter.check_capacity(1)
    sequential_path = output.with_name(f"{output.stem}-sequential{output.suffix}")
    parallel_path = output.with_name(f"{output.stem}-parallel{output.suffix}")
    await asyncio.to_thread(_validate_output, sequential_path, dataset_path, gates_path)
    await asyncio.to_thread(_validate_output, parallel_path, dataset_path, gates_path)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    started_at = datetime.now(UTC).isoformat()
    sequential_id, parallel_id = f"{run_id}-s", f"{run_id}-p"
    sequential = parallel = None
    active_arm = "before_sequential"
    try:
        if rate_limiter is not None:
            await rate_limiter.wait_until_clear()
        active_arm = "sequential"
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
            rate_limiter=rate_limiter,
            _run_id=sequential_id,
        )
        active_arm = "between_arms"
        if rate_limiter is not None:
            await rate_limiter.wait_until_clear()
        active_arm = "bounded_parallel"
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
            rate_limiter=rate_limiter,
            _run_id=parallel_id,
        )
    except (Exception, asyncio.CancelledError) as error:
        status = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
        interrupted = {
            "schema_version": "skill-concurrency-comparison-v1",
            "run_id": run_id,
            "started_at": started_at,
            "recorded_at": datetime.now(UTC).isoformat(),
            "status": status,
            "dataset_path": str(dataset_path),
            "split": split,
            "model_id": inference.capabilities.model_id,
            "live": True,
            "active_arm": active_arm,
            "sequential_run_id": sequential_id if active_arm != "before_sequential" else None,
            "parallel_run_id": parallel_id if active_arm == "bounded_parallel" else None,
            "sequential_status": sequential["status"] if sequential else None,
            "parallel_status": parallel["status"] if parallel else None,
            "failure_type": type(error).__name__,
            "compatible": False,
            "aggregate": {"speedup": None},
            "passed": False,
            "history_path": str(history_dir / f"skill-comparison-{run_id}.json"),
        }
        if await _publish_report(interrupted, output, history_dir, prefix="skill-comparison"):
            raise asyncio.CancelledError from None
        raise
    assert sequential is not None and parallel is not None
    parity = {
        name: {
            "same_quality_metrics": sequential["skills"][name]["metrics"]
            == parallel["skills"][name]["metrics"],
            "same_case_scores": [
                (case["id"], case["scores"], case["assertions"])
                for case in sequential["skills"][name]["cases"]
            ]
            == [
                (case["id"], case["scores"], case["assertions"])
                for case in parallel["skills"][name]["cases"]
            ],
            "same_domain_gates": {
                key: value
                for key, value in sequential["skills"][name]["quality_gates"].items()
                if key in {"precision", "recall", "document_coverage"}
            }
            == {
                key: value
                for key, value in parallel["skills"][name]["quality_gates"].items()
                if key in {"precision", "recall", "document_coverage"}
            }
            and sequential["skills"][name]["safety_gates"]["no_false_mandates"]
            == parallel["skills"][name]["safety_gates"]["no_false_mandates"],
            "sequential_metrics": sequential["skills"][name]["metrics"],
            "parallel_metrics": parallel["skills"][name]["metrics"],
            "sequential_gates": sequential["skills"][name]["quality_gates"],
            "parallel_gates": parallel["skills"][name]["quality_gates"],
            "sequential_performance": sequential["skills"][name]["performance"],
            "parallel_performance": parallel["skills"][name]["performance"],
        }
        for name in sequential["skills"]
    }
    ignored_configuration = {"comparison_arm", "max_concurrency", "max_model_calls"}
    conditions = [
        {
            key: value
            for key, value in evaluation["run_configuration"].items()
            if key not in ignored_configuration
        }
        for evaluation in (sequential, parallel)
    ]

    def cache_profile(evaluation: dict, name: str) -> list[tuple[str, int, int | None]]:
        return [
            (
                case["id"],
                case["result"]["usage"]["input_tokens"],
                case["result"]["usage"]["cached_input_tokens"],
            )
            for case in evaluation["skills"][name]["cases"]
        ]

    def provider_profile(evaluation: dict, name: str) -> list[tuple[str, list[str]]]:
        return [
            (case["id"], case["result"]["provider_model_ids"])
            for case in evaluation["skills"][name]["cases"]
        ]

    checks = {
        "complete_arms": sequential["status"] == parallel["status"] == "completed",
        "same_dataset_and_split": sequential["dataset_sha256"] == parallel["dataset_sha256"]
        and sequential["split"] == parallel["split"],
        "same_gates": sequential["gates_sha256"] == parallel["gates_sha256"],
        "same_execution_conditions": conditions[0] == conditions[1],
        "same_model": sequential["model_id"] == parallel["model_id"]
        and sequential["model_version"] == parallel["model_version"],
        "same_skills": set(sequential["skills"]) == set(parallel["skills"]),
        "same_skill_and_evaluator": all(
            sequential["skills"][name]["fingerprint"] == parallel["skills"][name]["fingerprint"]
            and sequential["skills"][name]["evaluator_fingerprint"]
            == parallel["skills"][name]["evaluator_fingerprint"]
            and sequential["skills"][name]["deployment"] == parallel["skills"][name]["deployment"]
            and sequential["skills"][name]["actual_model_ids"]
            == parallel["skills"][name]["actual_model_ids"]
            and provider_profile(sequential, name) == provider_profile(parallel, name)
            and sequential["skills"][name]["prompt_versions"]
            == parallel["skills"][name]["prompt_versions"]
            for name in parity
        ),
        "known_provider_model_identity": all(
            not case["result"]["requires_model"]
            or (
                case["result"]["usage"]["model_calls"] > 0
                and bool(case["result"]["provider_model_ids"])
                and (
                    case["result"]["provider_model_identity_complete"] is True
                    or (
                        case["result"]["provider_model_identity_complete"] is None
                        and case["result"]["usage"]["model_calls"] == 1
                    )
                )
            )
            for evaluation in (sequential, parallel)
            for item in evaluation["skills"].values()
            for case in item["cases"]
        ),
        "same_case_quality": all(
            item["same_quality_metrics"] and item["same_case_scores"] and item["same_domain_gates"]
            for item in parity.values()
        ),
        "uncached_evaluation_layer": all(
            item["performance"]["cache_hits"] == 0
            for evaluation in (sequential, parallel)
            for item in evaluation["skills"].values()
        ),
        "same_measured_provider_cache": all(
            sequential["skills"][name]["performance"]["provider_cached_input_tokens"] is not None
            and parallel["skills"][name]["performance"]["provider_cached_input_tokens"] is not None
            and cache_profile(sequential, name) == cache_profile(parallel, name)
            for name in parity
        ),
    }
    compatible = all(checks.values())
    seq_wall = sum(item["performance"]["wall_time_ms"] for item in sequential["skills"].values())
    parallel_wall = sum(item["performance"]["wall_time_ms"] for item in parallel["skills"].values())
    costs = [
        item["performance"]["measured_cost"]
        for evaluation in (sequential, parallel)
        for item in evaluation["skills"].values()
    ]

    def measured_total(evaluation: dict, key: str) -> int | float | None:
        values = [item["performance"][key] for item in evaluation["skills"].values()]
        return sum(values) if all(value is not None for value in values) else None

    report = {
        "schema_version": "skill-concurrency-comparison-v1",
        "run_id": run_id,
        "started_at": started_at,
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "completed" if compatible else "incomparable",
        "dataset_path": str(dataset_path),
        "dataset_sha256": sequential["dataset_sha256"],
        "split": split,
        "model_id": sequential["model_id"],
        "live": True,
        "cache_policy": "no skill-evaluation cache; provider cache status unknown",
        "max_concurrency": max_concurrency,
        "sequential_run_id": sequential["run_id"],
        "parallel_run_id": parallel["run_id"],
        "sequential_status": sequential["status"],
        "parallel_status": parallel["status"],
        "compatible": compatible,
        "compatibility_checks": checks,
        "incompatibility_reasons": [name for name, passed in checks.items() if not passed],
        "model_version": sequential["model_version"],
        "quality_parity": parity,
        "aggregate": {
            "sequential_wall_time_ms": seq_wall,
            "parallel_wall_time_ms": parallel_wall,
            "speedup": seq_wall / parallel_wall if compatible and parallel_wall else None,
            "sequential_model_requests": measured_total(sequential, "measured_model_requests"),
            "parallel_model_requests": measured_total(parallel, "measured_model_requests"),
            "sequential_failures": sequential["errors"],
            "parallel_failures": parallel["errors"],
            "sequential_throttles": measured_total(sequential, "throttle_count"),
            "parallel_throttles": measured_total(parallel, "throttle_count"),
            "combined_measured_cost": (
                sum(costs) if all(cost is not None for cost in costs) else None
            ),
        },
        "passed": compatible and sequential["passed"] and parallel["passed"],
    }
    report["history_path"] = str(history_dir / f"skill-comparison-{report['run_id']}.json")
    if await _publish_report(report, output, history_dir, prefix="skill-comparison"):
        raise asyncio.CancelledError
    return report


def _git_revision() -> dict[str, str | bool | None]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, check=True, text=True, timeout=3
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                capture_output=True,
                check=True,
                text=True,
                timeout=3,
            ).stdout.strip()
        )
        return {"revision": revision, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"revision": None, "dirty": None}
