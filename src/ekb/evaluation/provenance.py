from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal, Self, cast
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_evals import Case as EvalCase
from pydantic_evals import Dataset as EvalDataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from ekb.domain.execution import CheckOptions
from ekb.domain.models import (
    Candidate,
    ConceptDetail,
    ConceptVersion,
    Model,
    Occurrence,
    Relationship,
    RelationshipKind,
    Scope,
    SemanticType,
    Usage,
    content_hash,
    fingerprint,
    stable_id,
    total_usage,
)
from ekb.domain.resolution import KnowledgeSnapshot
from ekb.domain.traceability import (
    FrameworkReference,
    TraceabilityAssertion,
    TraceabilitySelection,
)
from ekb.evaluation.dataset import Split
from ekb.ports import EmbeddingPort, TraceabilityMapper, UnitOfWorkFactory
from ekb.ports.workflow import CheckContext, CheckResult
from ekb.stages.enrichment import Check
from ekb.stages.provenance import ControlMappingOptions, platform_alignment_check

type ProvenanceSplit = Split | Literal["all"]


class ProvenanceSource(Model):
    name: str = Field(min_length=1)
    location: str = Field(min_length=1)
    document_family: str | None = Field(default=None, min_length=1)


class ProvenanceAssertion(Model):
    statement: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    semantic_type: SemanticType
    source_key: str | None = None
    framework_references: tuple[FrameworkReference, ...] = ()
    source: ProvenanceSource


class ProvenanceCase(Model):
    id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    split: Split
    child_key: str = Field(min_length=1)
    parent_keys: tuple[str, ...] = Field(min_length=2, max_length=35)
    expected_parent_keys: tuple[str, ...] = Field(max_length=5)
    carried_parent_keys: tuple[str, ...] = Field(default=(), max_length=5)


class ProvenanceDataset(Model):
    version: str
    review_status: Literal["provisional", "independently_reviewed"]
    assertions: dict[str, ProvenanceAssertion] = Field(min_length=1)
    cases: tuple[ProvenanceCase, ...] = Field(min_length=10)

    @model_validator(mode="after")
    def isolated_splits(self) -> Self:
        if not {"validation", "test"} <= {case.split for case in self.cases}:
            raise ValueError("Provenance evaluation requires validation and test splits")
        families: dict[str, Split] = {}
        source_locations: dict[str, Split] = {}
        location_families: dict[str, str] = {}
        document_families: dict[str, Split] = {}
        texts: dict[str, Split] = {}
        for case in self.cases:
            referenced = {
                case.child_key,
                *case.parent_keys,
                *case.expected_parent_keys,
            }
            if not referenced <= self.assertions.keys():
                raise ValueError("Provenance case references an unknown assertion")
            if len(set(case.parent_keys)) != len(case.parent_keys):
                raise ValueError("Parent keys must be unique within a provenance case")
            child_source = self.assertions[case.child_key].source
            if child_source.document_family is not None:
                previous_family = location_families.get(child_source.location)
                if previous_family is not None and previous_family != child_source.document_family:
                    raise ValueError("A child source location must have one document family")
                location_families[child_source.location] = child_source.document_family
                previous_split = source_locations.get(child_source.location)
                if previous_split is not None and previous_split != case.split:
                    raise ValueError("Child source locations cannot cross dataset splits")
                source_locations[child_source.location] = case.split
                previous_split = document_families.get(child_source.document_family)
                if previous_split is not None and previous_split != case.split:
                    raise ValueError("Child document families cannot cross dataset splits")
                document_families[child_source.document_family] = case.split
            if not set(case.carried_parent_keys) <= set(case.parent_keys):
                raise ValueError("Carried provenance parents must be supplied candidates")
            if case.family in families and families[case.family] != case.split:
                raise ValueError("Provenance families cannot cross dataset splits")
            families[case.family] = case.split
            normalized = " ".join(self.assertions[case.child_key].statement.casefold().split())
            if normalized in texts and texts[normalized] != case.split:
                raise ValueError("Duplicate child assertions cannot cross dataset splits")
            texts[normalized] = case.split
        return self

    @property
    def document_family_isolated(self) -> bool:
        return all(
            self.assertions[case.child_key].source.document_family is not None
            for case in self.cases
        )


class ProvenanceExecution(Model):
    selection: TraceabilitySelection | None = None
    usage: Usage = Field(default_factory=Usage)
    model_calls: int = 0
    error: str | None = None


class ProductionExecution(Model):
    deterministic_parent_keys: tuple[str, ...] = ()
    model_selected_parent_keys: tuple[str, ...] = ()
    carried_parent_keys: tuple[str, ...] = ()
    candidate_parent_keys: tuple[str, ...] = ()
    unresolved: bool = False
    usage: Usage = Field(default_factory=Usage)
    model_calls: int = 0
    embedding_calls: int = 0
    latency_ms: float = 0
    error: str | None = None


class ProvenanceGates(Model):
    version: str
    minimum_precision: float = Field(ge=0, le=1)
    minimum_recall: float = Field(ge=0, le=1)
    maximum_p95_ms: float = Field(gt=0)
    maximum_estimated_cost: float = Field(gt=0)


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
        valid = predicted & expected
        return {
            "exact": (
                not ctx.output.error
                and not (predicted - expected)
                and bool(predicted) == bool(expected)
            ),
            "tp": int(bool(valid)),
            "fp": len(predicted - expected),
            "fn": int(bool(expected) and not valid),
        }


@dataclass
class ProductionProvenanceQuality(Evaluator[str, ProductionExecution, ProvenanceCase]):
    def get_evaluator_version(self) -> str:
        return "production-provenance-quality-v2"

    def evaluate(
        self, ctx: EvaluatorContext[str, ProductionExecution, ProvenanceCase]
    ) -> dict[str, bool | int]:
        if ctx.metadata is None:
            return {"exact": False, "tp": 0, "fp": 0, "fn": 0}
        case = ctx.metadata
        execution = ctx.output
        predicted = (
            set(execution.deterministic_parent_keys)
            | set(execution.model_selected_parent_keys)
            | set(execution.carried_parent_keys)
        )
        expected = set(case.expected_parent_keys)
        valid = predicted & expected
        return {
            "exact": (
                not execution.error
                and not (predicted - expected)
                and bool(predicted) == bool(expected)
            ),
            "tp": int(bool(valid)),
            "fp": len(predicted - expected),
            "fn": int(bool(expected) and not valid),
        }


class _EvaluationStore:
    def __init__(self) -> None:
        self.workflow = self
        self._entries: dict[str, object] = {}

    def __call__(self) -> _EvaluationStore:
        return self

    async def __aenter__(self) -> _EvaluationStore:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def semantic_cache(self, keys: list[str]) -> dict[str, object]:
        return {key: self._entries[key] for key in keys if key in self._entries}

    async def save_semantic_cache(self, entries: dict[str, object]) -> None:
        self._entries.update(entries)


def _production_context(
    case: ProvenanceCase,
    dataset: ProvenanceDataset,
    corpus_keys: tuple[str, ...],
    options: ControlMappingOptions,
    embedding_neighbors: int,
) -> tuple[
    CheckContext,
    dict[UUID, str],
]:
    keys = tuple(sorted({case.child_key, *corpus_keys}))
    concepts: dict[str, ConceptDetail] = {}
    versions: dict[str, ConceptVersion] = {}
    occurrences: dict[str, Occurrence] = {}
    for key in keys:
        value = dataset.assertions[key]
        concept_id = stable_id("provenance-eval", case.id, key)
        version_id = stable_id("provenance-eval-version", case.id, key)
        occurrence_id = stable_id("provenance-eval-occurrence", case.id, key)
        segment_id = stable_id("provenance-eval-segment", case.id, key)
        version = ConceptVersion(
            id=version_id,
            concept_id=concept_id,
            candidate=Candidate(
                statement=value.statement,
                excerpt=value.excerpt,
                semantic_type=value.semantic_type,
                source_key=value.source_key,
                scope=(
                    Scope(platform="Azure")
                    if value.semantic_type is SemanticType.PLATFORM_REQUIREMENT
                    else Scope()
                ),
            ),
        )
        occurrence = Occurrence(
            id=occurrence_id,
            concept_version_id=version_id,
            segment_id=segment_id,
            excerpt=value.excerpt,
            start=0,
            end=len(value.excerpt),
            extraction_metadata={
                "framework_references": [
                    reference.model_dump(mode="json") for reference in value.framework_references
                ]
            },
        )
        versions[key] = version
        occurrences[key] = occurrence
        concepts[key] = ConceptDetail(
            id=concept_id,
            versions=[version],
            occurrences=[occurrence],
            relationships=[],
        )

    child = concepts[case.child_key]
    for parent_key in case.carried_parent_keys:
        child.relationships.append(
            Relationship(
                id=stable_id("provenance-eval-carried", case.id, parent_key),
                from_concept_id=child.id,
                to_concept_id=concepts[parent_key].id,
                kind=RelationshipKind.REFINES,
                occurrence_id=occurrences[case.child_key].id,
            )
        )

    all_concepts = tuple(concepts.values())
    snapshot = KnowledgeSnapshot(
        revision=dataset.version,
        governance_revision=dataset.version,
        concepts=all_concepts,
        current_version_ids=frozenset(version.id for version in versions.values()),
    )
    context = CheckContext(
        run_id=stable_id("provenance-eval-run", case.id),
        input_revision=content_hash(
            json.dumps([dataset.version, case.id], separators=(",", ":")).encode()
        ),
        snapshot=snapshot,
        options=CheckOptions(
            embedding_neighbors=embedding_neighbors,
            parameters=options.model_dump(mode="json"),
        ),
    )
    parent_by_id = {concepts[key].id: key for key in corpus_keys}
    return (
        context,
        parent_by_id,
    )


async def _run_production_case(
    case_id: str,
    *,
    cases: dict[str, ProvenanceCase],
    dataset: ProvenanceDataset,
    corpus_keys: tuple[str, ...],
    check: Check,
    options: ControlMappingOptions,
    embedding_neighbors: int,
    retrieved: dict[tuple[UUID, UUID], tuple[UUID, ...]],
    completed: dict[str, ProductionExecution],
) -> ProductionExecution:
    case = cases[case_id]
    started = time.perf_counter()
    try:
        context, parent_by_id = _production_context(
            case, dataset, corpus_keys, options, embedding_neighbors
        )
        result: CheckResult = await check.evaluate(context)
        child_id = stable_id("provenance-eval", case.id, case.child_key)
        candidates = retrieved.get((context.run_id, child_id))
        if candidates is None and not case.carried_parent_keys:
            raise ValueError("Production retrieval did not report this child")
        candidate_keys = tuple(parent_by_id[parent_id] for parent_id in candidates or ())

        def parent_keys(*, deterministic_only: bool) -> tuple[str, ...]:
            return tuple(
                parent_by_id[finding.concept_ids[1]]
                for finding in result.findings
                if finding.deterministic is deterministic_only
                and len(finding.concept_ids) > 1
                and finding.concept_ids[1] in parent_by_id
            )

        deterministic = parent_keys(deterministic_only=True)
        model_selected = parent_keys(deterministic_only=False)
        carried = case.carried_parent_keys
        execution = ProductionExecution(
            deterministic_parent_keys=deterministic,
            model_selected_parent_keys=model_selected,
            carried_parent_keys=carried,
            candidate_parent_keys=candidate_keys,
            unresolved=not (deterministic or model_selected or carried),
            usage=result.usage,
            model_calls=result.model_calls,
            embedding_calls=result.embedding_calls,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    except Exception as error:
        execution = ProductionExecution(
            carried_parent_keys=case.carried_parent_keys,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=type(error).__name__,
        )
    completed[case_id] = execution
    return execution


async def evaluate_provenance_production(
    dataset_path: Path,
    output: Path,
    mapper: TraceabilityMapper,
    embedding_provider: EmbeddingPort | None,
    *,
    split: ProvenanceSplit = "test",
    max_cases: int = 100,
    max_concurrency: int = 4,
    confidence_threshold: float = 0.72,
    max_parents: int = 3,
    embedding_neighbors: int = 20,
    gates_path: Path = Path("evals") / "gates.json",
    history_dir: Path = Path("data") / "evaluation-history",
) -> dict[str, Any]:
    if not 1 <= max_cases <= 1000:
        raise ValueError("Production provenance evaluation supports 1 to 1000 cases")
    if not 1 <= max_concurrency <= 16:
        raise ValueError("Production provenance evaluation supports concurrency from 1 to 16")
    CheckOptions(embedding_neighbors=embedding_neighbors)
    dataset_raw, dataset = await asyncio.to_thread(load_provenance_dataset, dataset_path)
    gates_raw = await asyncio.to_thread(gates_path.read_bytes)
    gates_data = json.loads(gates_raw)
    gates = ProvenanceGates.model_validate(
        {
            key: gates_data[key]
            for key in (
                "version",
                "minimum_precision",
                "minimum_recall",
                "maximum_p95_ms",
                "maximum_estimated_cost",
            )
        }
    )
    selected_cases = [case for case in dataset.cases if split == "all" or case.split == split]
    cases = selected_cases[:max_cases]
    if not cases:
        raise ValueError("No provenance evaluation cases match the selected split")
    options = ControlMappingOptions(
        confidence_threshold=confidence_threshold,
        max_parents=max_parents,
        semantic_neighbors=embedding_neighbors,
    )
    corpus_keys = tuple(
        sorted(
            key
            for key, value in dataset.assertions.items()
            if value.semantic_type
            in {SemanticType.CONCEPTUAL_REQUIREMENT, SemanticType.TECHNICAL_REQUIREMENT}
        )
    )
    retrieved: dict[tuple[UUID, UUID], tuple[UUID, ...]] = {}

    def observe_candidates(run_id: UUID, child_id: UUID, candidates: tuple[UUID, ...]) -> None:
        retrieved[(run_id, child_id)] = candidates

    check = platform_alignment_check(
        cast(UnitOfWorkFactory, _EvaluationStore()),
        mapper,
        embedding_provider,
        concurrency=max_concurrency,
        observe_candidates=observe_candidates,
    )
    cases_by_id = {case.id: case for case in cases}
    completed: dict[str, ProductionExecution] = {}
    evaluator = ProductionProvenanceQuality()
    eval_dataset = EvalDataset[str, ProductionExecution, ProvenanceCase](
        name=f"control-traceability-production-{dataset.version}-{split}",
        cases=[
            EvalCase(
                name=case.id,
                inputs=case.id,
                metadata=case,
                evaluators=[evaluator],
            )
            for case in cases
        ],
    )
    provider_fingerprint = fingerprint(
        mapper=mapper.fingerprint,
        embeddings=embedding_provider.fingerprint if embedding_provider else None,
        retrieval="production-lexical" if embedding_provider is None else "production-embeddings",
    )
    selection_policy_fingerprint = fingerprint(
        check_version=check.spec.version,
        confidence_threshold=options.confidence_threshold,
        max_parents=options.max_parents,
        embedding_neighbors=embedding_neighbors,
        candidate_universe="all-dataset-requirements",
    )
    evaluator_fingerprint = fingerprint(
        evaluator=evaluator.get_evaluator_version(),
        runner="pydantic-evals",
        runner_version=version("pydantic-evals"),
    )
    framework = await eval_dataset.evaluate(
        lambda case_id: _run_production_case(
            case_id,
            cases=cases_by_id,
            dataset=dataset,
            corpus_keys=corpus_keys,
            check=check,
            options=options,
            embedding_neighbors=embedding_neighbors,
            retrieved=retrieved,
            completed=completed,
        ),
        max_concurrency=max_concurrency,
        progress=False,
        task_name="ekb-control-traceability-production",
        metadata={
            "dataset_version": dataset.version,
            "dataset_sha256": content_hash(dataset_raw),
            "evaluator_fingerprint": evaluator_fingerprint,
            "selection_policy_fingerprint": selection_policy_fingerprint,
            "provider_fingerprint": provider_fingerprint,
            "split": split,
        },
    )
    failures = {failure.name for failure in framework.failures}
    failures.update(case.name for case in framework.cases if case.evaluator_failures)
    tp = fp = fn = exact = 0
    retrieval_hits = retrieval_total = 0
    case_reports: list[dict[str, Any]] = []
    outcome_case_counts = {
        "deterministic": 0,
        "model_selected": 0,
        "carried": 0,
        "unresolved": 0,
        "failed": 0,
    }
    outcome_link_counts = {"deterministic": 0, "model_selected": 0, "carried": 0}
    for case in cases:
        execution = completed.get(case.id, ProductionExecution(error="missing_result"))
        if execution.error is not None:
            failures.add(case.id)
        predicted = (
            set(execution.deterministic_parent_keys)
            | set(execution.model_selected_parent_keys)
            | set(execution.carried_parent_keys)
        )
        expected = set(case.expected_parent_keys)
        valid = predicted & expected
        tp += int(bool(valid))
        fp += len(predicted - expected)
        fn += int(bool(expected) and not valid)
        exact += (
            not (predicted - expected)
            and bool(predicted) == bool(expected)
            and execution.error is None
            and case.id not in failures
        )
        if not case.carried_parent_keys and expected:
            retrieval_total += 1
            retrieval_hits += bool(expected & set(execution.candidate_parent_keys))
        for category, keys in (
            ("deterministic", execution.deterministic_parent_keys),
            ("model_selected", execution.model_selected_parent_keys),
            ("carried", execution.carried_parent_keys),
        ):
            outcome_link_counts[category] += len(keys)
            outcome_case_counts[category] += bool(keys)
        outcome_case_counts["unresolved"] += execution.unresolved
        outcome_case_counts["failed"] += execution.error is not None

        def evidence(keys: set[str]) -> list[dict[str, str]]:
            return [
                {
                    "key": key,
                    "statement": dataset.assertions[key].statement,
                    "excerpt": dataset.assertions[key].excerpt,
                    "source_name": dataset.assertions[key].source.name,
                    "source_location": dataset.assertions[key].source.location,
                }
                for key in sorted(keys)
            ]

        case_reports.append(
            {
                "id": case.id,
                "expected": sorted(expected),
                "predicted": sorted(predicted),
                "deterministic": list(execution.deterministic_parent_keys),
                "model_selected": list(execution.model_selected_parent_keys),
                "carried": list(execution.carried_parent_keys),
                "unresolved": execution.unresolved,
                "retrieved_candidates": list(execution.candidate_parent_keys),
                "expected_evidence": evidence(expected),
                "predicted_evidence": evidence(predicted),
                "child_evidence": evidence({case.child_key}),
                "error": execution.error,
                "latency_ms": execution.latency_ms,
            }
        )
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    executions = list(completed.values())
    usage = total_usage([execution.usage for execution in executions])
    case_costs = [
        execution.usage.cost
        if execution.error or execution.model_calls or execution.embedding_calls
        else 0.0
        for execution in executions
    ]
    usage = usage.model_copy(
        update={
            "model_calls": sum(execution.model_calls for execution in executions),
            "cost": (
                sum(cost for cost in case_costs if cost is not None)
                if all(cost is not None for cost in case_costs)
                else None
            ),
        }
    )
    latencies = sorted(execution.latency_ms for execution in completed.values())
    p95_latency = latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None
    quality_gates = {
        "precision": precision >= gates.minimum_precision,
        "recall": recall >= gates.minimum_recall,
    }
    model_calls = sum(execution.model_calls for execution in executions)
    performance_gates = {
        "latency": p95_latency is not None and p95_latency <= gates.maximum_p95_ms,
        "priced_cost": usage.cost is not None and usage.cost <= gates.maximum_estimated_cost,
    }
    passed = not failures and all(quality_gates.values()) and all(performance_gates.values())
    qualification_gates = {
        "document_family_isolated": dataset.document_family_isolated,
        "held_out_test": split == "test",
        "complete_split": len(cases) == len(selected_cases),
    }
    report: dict[str, Any] = {
        "evaluation_kind": "production_end_to_end_retrieval_selection",
        "passed": passed,
        "qualified": passed and model_calls > 0 and all(qualification_gates.values()),
        "qualification_gates": qualification_gates,
        "document_family_isolated": dataset.document_family_isolated,
        "model_backed": model_calls > 0,
        "dataset_version": dataset.version,
        "dataset_sha256": content_hash(dataset_raw),
        "dataset_fingerprint": content_hash(dataset_raw),
        "review_status": dataset.review_status,
        "authority_status": "review_only_no_automatic_approval",
        "split": split,
        "cases": len(cases),
        "exact_cases": exact,
        "precision": precision,
        "recall": recall,
        "retrieval_recall_at_k": retrieval_hits / retrieval_total if retrieval_total else 1.0,
        "retrieval_k": max((len(item.candidate_parent_keys) for item in executions), default=0),
        "semantic_neighbors": options.semantic_neighbors,
        "lexical_neighbors": options.lexical_neighbors,
        "retrieval_scope": "all_dataset_requirements",
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "failures": sorted(failures),
        "case_results": case_reports,
        "outcome_case_counts": outcome_case_counts,
        "outcome_link_counts": outcome_link_counts,
        "confidence_threshold": options.confidence_threshold,
        "max_parents": options.max_parents,
        "selection_policy_fingerprint": selection_policy_fingerprint,
        "provider_fingerprint": provider_fingerprint,
        "mapper_fingerprint": mapper.fingerprint,
        "embedding_fingerprint": embedding_provider.fingerprint if embedding_provider else None,
        "evaluator_fingerprint": evaluator_fingerprint,
        "pydantic_evals_version": version("pydantic-evals"),
        "quality_gates": quality_gates,
        "performance_gates": performance_gates,
        "gates_version": gates.version,
        "gates_sha256": content_hash(gates_raw),
        "p95_latency_ms": p95_latency,
        "model_calls": model_calls,
        "embedding_calls": sum(execution.embedding_calls for execution in completed.values()),
        "usage": usage.model_dump(mode="json"),
    }
    serialized = json.dumps(report, indent=2) + "\n"
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, serialized, encoding="utf-8")
    await asyncio.to_thread(history_dir.mkdir, parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    history = history_dir / (
        f"provenance-production-{stamp}-{content_hash(serialized.encode())[:8]}.json"
    )
    await asyncio.to_thread(history.write_text, serialized, encoding="utf-8")
    report["history"] = str(history)
    return report


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
        source_key=value.source_key,
        framework_references=value.framework_references,
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
    max_cases: int = 1000,
    max_concurrency: int = 4,
    history_dir: Path = Path("data") / "evaluation-history",
) -> dict[str, Any]:
    raw, dataset = await asyncio.to_thread(load_provenance_dataset, dataset_path)
    cases = [case for case in dataset.cases if split == "all" or case.split == split][:max_cases]
    if not cases:
        raise ValueError("No provenance evaluation cases match the selected split")
    by_id = {case.id: case for case in cases}
    completed: dict[str, ProvenanceExecution] = {}
    evaluator = ProvenanceQuality()
    evaluator_fingerprint = fingerprint(
        evaluator=evaluator.get_evaluator_version(),
        runner="pydantic-evals",
        runner_version=version("pydantic-evals"),
    )
    provider_fingerprint = fingerprint(mapper=mapper.fingerprint)
    selection_policy_fingerprint = fingerprint(kind="frozen-candidate-pool-mapper-component-v1")
    eval_dataset = EvalDataset[str, ProvenanceExecution, ProvenanceCase](
        name=f"control-traceability-{dataset.version}-{split}",
        cases=[
            EvalCase(
                name=case.id,
                inputs=case.id,
                metadata=case,
                evaluators=[evaluator],
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
            "evaluator_fingerprint": evaluator_fingerprint,
            "selection_policy_fingerprint": selection_policy_fingerprint,
            "provider_fingerprint": provider_fingerprint,
            "split": split,
        },
    )
    failures = {failure.name for failure in framework.failures}
    failures.update(case.name for case in framework.cases if case.evaluator_failures)
    tp = fp = fn = exact = retrieval_expected = retrieval_available = 0
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
        available = expected & set(case.parent_keys)
        valid = predicted & expected
        retrieval_expected += int(bool(expected))
        retrieval_available += int(bool(available))
        tp += int(bool(valid))
        fp += len(predicted - expected)
        fn += int(bool(expected) and not valid)
        exact += (
            not (predicted - expected)
            and bool(predicted) == bool(expected)
            and execution.error is None
            and case.id not in failures
        )
        case_reports.append(
            {
                "id": case.id,
                "expected": sorted(expected),
                "predicted": sorted(predicted),
                "missing_candidates": sorted(expected - set(case.parent_keys)),
                "unresolved_reason": (
                    execution.selection.unresolved_reason if execution.selection else None
                ),
                "rationales": {
                    by_parent[link.parent_concept_id]: link.rationale
                    for link in (execution.selection.links if execution.selection else ())
                    if link.parent_concept_id in by_parent
                },
                "error": execution.error,
            }
        )
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    model_calls = sum(execution.model_calls for execution in completed.values())
    usage = total_usage([execution.usage for execution in completed.values()])
    report: dict[str, Any] = {
        "evaluation_kind": "frozen_pool_mapper_component",
        "qualification_scope": "mapper_component_only",
        "production_path_qualified": False,
        "passed": not failures and precision >= 0.9 and recall >= 0.9,
        "dataset_version": dataset.version,
        "dataset_sha256": content_hash(raw),
        "dataset_fingerprint": content_hash(raw),
        "review_status": dataset.review_status,
        "document_family_isolated": dataset.document_family_isolated,
        "split": split,
        "cases": len(cases),
        "exact_cases": exact,
        "precision": precision,
        "recall": recall,
        "retrieval_recall": (
            retrieval_available / retrieval_expected if retrieval_expected else 1.0
        ),
        "retrieval_expected": retrieval_expected,
        "retrieval_available": retrieval_available,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "failures": sorted(failures),
        "case_results": case_reports,
        "mapper_fingerprint": mapper.fingerprint,
        "provider_fingerprint": provider_fingerprint,
        "selection_policy_fingerprint": selection_policy_fingerprint,
        "evaluator_fingerprint": evaluator_fingerprint,
        "pydantic_evals_version": version("pydantic-evals"),
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
