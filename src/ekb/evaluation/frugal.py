from __future__ import annotations

import asyncio
import math
import time
from difflib import SequenceMatcher
from importlib.metadata import version
from pathlib import Path

from pydantic import Field
from pydantic_evals import Case as EvalCase
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from ekb.domain.models import Model, content_hash, now
from ekb.domain.reconciliation import (
    EmbeddingResult,
    SemanticAssertion,
    SemanticOutcome,
    Verification,
    structural_verification,
)
from ekb.evaluation import evaluate, ratio
from ekb.evaluation.dataset import (
    Seed,
    Split,
    TeacherDataset,
    extraction_cases,
    load_dataset,
    pair_cases,
    write_json,
)
from ekb.evaluation.runner import evaluate_sequential, serialize_results
from ekb.ports import EmbeddingPort, InferencePort, SemanticVerifier
from ekb.services.retrieval import nearest

type SemanticPair = tuple[str, SemanticAssertion, SemanticAssertion, SemanticOutcome]


class Gates(Model):
    version: str
    minimum_precision: float = Field(ge=0, le=1)
    minimum_recall: float = Field(ge=0, le=1)
    minimum_scope_accuracy: float = Field(ge=0, le=1)
    minimum_pair_accuracy: float = Field(ge=0, le=1)
    minimum_retrieval_recall: float = Field(ge=0, le=1)
    minimum_mrr: float = Field(ge=0, le=1)
    maximum_p95_ms: float = Field(gt=0)
    maximum_tokens: int = Field(gt=0)
    maximum_estimated_cost: float = Field(gt=0)
    retrieval_k: int = Field(ge=1, le=20)


class _SemanticOutcome(Model):
    error: str | None = None


class _SemanticEvaluator(Evaluator[str, _SemanticOutcome, None]):
    def __init__(self, pairs: dict[str, SemanticPair], results: dict[str, Verification]):
        self._pairs = pairs
        self._results = results

    def evaluate(self, ctx: EvaluatorContext[str, _SemanticOutcome, None]) -> dict[str, bool]:
        result = self._results.get(ctx.inputs)
        if result is None:
            return {"valid": False, "correct": False, "no_false_equivalence": False}
        _, old, new, expected = self._pairs[ctx.inputs]
        judgment = result.judgment
        valid = (
            bool(judgment.old_excerpt.strip())
            and bool(judgment.new_excerpt.strip())
            and judgment.old_excerpt in old.excerpt
            and judgment.new_excerpt in new.excerpt
            and result.completed
        )
        false_equivalence = judgment.outcome in {
            "equivalent",
            "exact_duplicate",
        } and expected not in {
            "equivalent",
            "exact_duplicate",
        }
        return {
            "valid": valid,
            "correct": valid and judgment.outcome == expected,
            "no_false_equivalence": not false_equivalence,
        }


class _EmbeddingOutcome(Model):
    requested: int
    received: int | None = None
    error: str | None = None


class _EmbeddingEvaluator(Evaluator[str, _EmbeddingOutcome, None]):
    def evaluate(self, ctx: EvaluatorContext[str, _EmbeddingOutcome, None]) -> dict[str, bool]:
        return {
            "vector_count_matches": ctx.output.error is None
            and ctx.output.received == ctx.output.requested
        }


class _RetrievalOutcome(Model):
    error: str | None = None


class _RetrievalEvaluator(Evaluator[str, _RetrievalOutcome, None]):
    def __init__(self, seeds: dict[str, Seed], rankings: dict[str, list[tuple[str, float]]]):
        self._seeds = seeds
        self._rankings = rankings

    def evaluate(
        self, ctx: EvaluatorContext[str, _RetrievalOutcome, None]
    ) -> dict[str, bool | float]:
        ranked = self._rankings.get(ctx.inputs)
        if ranked is None:
            return {"ranked": False, "recall": 0.0, "reciprocal_rank": 0.0}
        seed = self._seeds[ctx.inputs]
        relevant = {seed.id, *seed.relevant_seed_ids}
        ids = [key for key, _ in ranked]
        rank = next((i + 1 for i, key in enumerate(ids) if key in relevant), None)
        return {
            "ranked": True,
            "recall": len(relevant.intersection(ids)) / len(relevant),
            "reciprocal_rank": 1 / rank if rank else 0.0,
        }


async def retrieval(
    dataset: TeacherDataset,
    split: Split,
    k: int,
    embeddings: EmbeddingPort | None,
) -> dict:
    seeds = dataset.selected(split)
    texts = list(dict.fromkeys([s.quote for s in seeds] + [s.query for s in seeds]))
    vectors: dict[str, tuple[float, ...]] = {}
    calls = tokens = 0
    embedding_cost: float | None = 0.0
    embedding_usage_complete: bool | None = True
    started = time.monotonic()
    embedding_evaluation = None
    if embeddings:
        provider = embeddings
        results: dict[str, EmbeddingResult] = {}
        embedding_errors: dict[str, Exception] = {}

        async def embed(key: str) -> _EmbeddingOutcome:
            try:
                result = await provider.embed(texts)
                results[key] = result
                return _EmbeddingOutcome(requested=len(texts), received=len(result.vectors))
            except Exception as error:
                embedding_errors[key] = error
                return _EmbeddingOutcome(requested=len(texts), error=type(error).__name__)

        embedding_cases = await evaluate_sequential(
            [
                EvalCase[str, _EmbeddingOutcome, None](
                    name="embedding-0",
                    inputs="embedding-0",
                    evaluators=[_EmbeddingEvaluator()],
                )
            ],
            embed,
            name="embedding-batch",
        )
        evaluated_embedding = embedding_cases[0]
        if embedding_errors:
            raise next(iter(embedding_errors.values()))
        if not evaluated_embedding.assertions["vector_count_matches"].value:
            raise ValueError("Embedding provider omitted requested vectors")
        result = results[evaluated_embedding.inputs]
        vectors = dict(zip(texts, result.vectors, strict=True))
        calls, tokens = result.calls, result.input_tokens
        embedding_cost, embedding_usage_complete = result.cost, result.usage_complete
        embedding_evaluation = {
            "scores": serialize_results(evaluated_embedding.scores),
            "assertions": serialize_results(evaluated_embedding.assertions),
        }
    seeds_by_key = {f"query-{index}": seed for index, seed in enumerate(seeds)}
    rankings: dict[str, list[tuple[str, float]]] = {}
    query_errors: dict[str, Exception] = {}
    evaluator = _RetrievalEvaluator(seeds_by_key, rankings)

    async def rank_query(key: str) -> _RetrievalOutcome:
        if query_errors:
            return _RetrievalOutcome(error="Skipped")
        seed = seeds_by_key[key]
        try:
            if embeddings:
                ranked = await asyncio.to_thread(
                    nearest,
                    vectors[seed.query],
                    {s.id: vectors[s.quote] for s in seeds},
                    k,
                )
            else:
                ranked = sorted(
                    (
                        (
                            s.id,
                            SequenceMatcher(
                                None, seed.query.casefold(), s.quote.casefold()
                            ).ratio(),
                        )
                        for s in seeds
                    ),
                    key=lambda x: (-x[1], x[0]),
                )[:k]
            rankings[key] = ranked
            return _RetrievalOutcome()
        except Exception as error:
            query_errors[key] = error
            return _RetrievalOutcome(error=type(error).__name__)

    evaluated_queries = await evaluate_sequential(
        [
            EvalCase[str, _RetrievalOutcome, None](name=key, inputs=key, evaluators=[evaluator])
            for key in seeds_by_key
        ],
        rank_query,
        name="retrieval-queries",
    )
    if query_errors:
        raise next(iter(query_errors.values()))
    rows = []
    for evaluated_query in evaluated_queries:
        seed = seeds_by_key[evaluated_query.inputs]
        relevant = {seed.id, *seed.relevant_seed_ids}
        ids = [key for key, _ in rankings[evaluated_query.inputs]]
        rows.append(
            {
                "id": seed.id,
                "expected": sorted(relevant),
                "retrieved": ids,
                "recall": evaluated_query.scores["recall"].value,
                "reciprocal_rank": evaluated_query.scores["reciprocal_rank"].value,
                "scores": serialize_results(evaluated_query.scores),
                "assertions": serialize_results(evaluated_query.assertions),
            }
        )
    return {
        "provider": embeddings.fingerprint if embeddings else "lexical-rules",
        "model_id": embeddings.model_id if embeddings else None,
        "evaluation_runner": "pydantic-evals",
        "evaluation_runner_version": version("pydantic-evals"),
        "recall_at_k": sum(r["recall"] for r in rows) / len(rows),
        "mrr": sum(r["reciprocal_rank"] for r in rows) / len(rows),
        "k": k,
        "queries": rows,
        "calls": calls,
        "input_tokens": tokens,
        "cost": embedding_cost,
        "usage_complete": embedding_usage_complete,
        "latency_ms": (time.monotonic() - started) * 1000,
        "embedding_evaluation": embedding_evaluation,
    }


async def qualify(
    dataset_path: Path,
    gates_path: Path,
    output: Path,
    inference: InferencePort,
    verifier: SemanticVerifier | None = None,
    embeddings: EmbeddingPort | None = None,
    *,
    live: bool = False,
    split: Split = "test",
    max_cases: int = 100,
    embedding_input_price: float | None = None,
) -> dict:
    dataset = await asyncio.to_thread(load_dataset, dataset_path)
    gates = Gates.model_validate_json(await asyncio.to_thread(gates_path.read_bytes))
    cases, pairs = extraction_cases(dataset, split), pair_cases(dataset, split)
    if len(cases) + len(pairs) > max_cases:
        raise ValueError("Evaluation case budget exceeded before making any calls")
    if live and (
        verifier is None
        or embeddings is None
        or inference.capabilities.model_id.startswith("offline-")
    ):
        raise ValueError("Live qualification requires configured student, verifier and embeddings")
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    inputs = output.with_name(output.stem + "-inputs.json")
    await write_json(inputs, {"cases": [c.model_dump(mode="json") for c in cases]})
    extraction = await evaluate(
        inputs, output.with_name(output.stem + "-extraction.json"), inference
    )
    judgments = []
    calls = tokens = 0
    costs = []
    usage_complete = True
    latencies = [r["latency_ms"] for r in extraction["documents"]]
    errors = sum("error" in r for r in extraction["documents"])
    for row in extraction["documents"]:
        usage = row.get("result", {}).get("usage", {})
        usage_complete &= usage.get("input_tokens", 0) > 0 and usage.get("output_tokens", 0) > 0
        tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        costs.append(usage.get("cost"))
    pairs_by_key = {f"semantic-{index}": pair for index, pair in enumerate(pairs)}
    semantic_results: dict[str, Verification] = {}
    evaluator = _SemanticEvaluator(pairs_by_key, semantic_results)

    async def verify_pair(key: str) -> _SemanticOutcome:
        _, old, new, _ = pairs_by_key[key]
        try:
            baseline = structural_verification(old, new)
            result = (
                await verifier.verify(old, new)
                if verifier
                and baseline.judgment.outcome != "exact_duplicate"
                and old.scope == new.scope
                else baseline
            )
            semantic_results[key] = result
            return _SemanticOutcome()
        except Exception as error:
            return _SemanticOutcome(error=type(error).__name__)

    evaluated_pairs = await evaluate_sequential(
        [
            EvalCase[str, _SemanticOutcome, None](name=key, inputs=key, evaluators=[evaluator])
            for key in pairs_by_key
        ],
        verify_pair,
        name="semantic-pairs",
    )
    for pair, evaluated_pair in zip(pairs, evaluated_pairs, strict=True):
        identifier, _, _, expected = pair
        valid = evaluated_pair.assertions["valid"].value
        if evaluated_pair.output.error is None:
            result = semantic_results[evaluated_pair.inputs]
            calls += result.model_calls
            tokens += result.usage.input_tokens + result.usage.output_tokens
            if result.model_calls:
                usage_complete &= result.usage.input_tokens > 0 and result.usage.output_tokens > 0
                costs.append(result.usage.cost)
            errors += not valid
            judgments.append(
                {
                    "id": identifier,
                    "expected": expected,
                    "actual": result.judgment.outcome,
                    "valid": valid,
                    "false_equivalence": not evaluated_pair.assertions[
                        "no_false_equivalence"
                    ].value,
                    "result": result.model_dump(mode="json"),
                    "scores": serialize_results(evaluated_pair.scores),
                    "assertions": serialize_results(evaluated_pair.assertions),
                }
            )
        else:
            errors += 1
            usage_complete = False
            judgments.append(
                {
                    "id": identifier,
                    "expected": expected,
                    "error": evaluated_pair.output.error,
                    "valid": False,
                    "scores": serialize_results(evaluated_pair.scores),
                    "assertions": serialize_results(evaluated_pair.assertions),
                }
            )
        latencies.append(evaluated_pair.task_duration * 1000)
    retrieval_report = await retrieval(dataset, split, gates.retrieval_k, embeddings)
    tokens += retrieval_report["input_tokens"]
    if embeddings:
        embedding_calls = retrieval_report["calls"]
        embedding_tokens = retrieval_report["input_tokens"]
        # Positive partial token totals do not establish that retries were metered.
        embedding_complete = retrieval_report["usage_complete"] is True and (
            (embedding_calls == 0 and embedding_tokens == 0)
            or (embedding_calls > 0 and embedding_tokens > 0)
        )
        usage_complete &= embedding_complete
        embedding_cost = retrieval_report["cost"] if embedding_complete else None
        if embedding_complete and embedding_cost is None:
            if embedding_calls == 0:
                embedding_cost = 0.0
            elif embedding_input_price is not None:
                embedding_cost = embedding_tokens * embedding_input_price / 1_000_000
        costs.append(embedding_cost)
    metrics = extraction["metrics"]
    categories = [value for value in metrics.values() if isinstance(value, dict) and "tp" in value]
    total_tp = sum(value["tp"] for value in categories)
    total_fp = sum(value["fp"] for value in categories)
    total_fn = sum(value["fn"] for value in categories)
    precision = ratio(total_tp, total_tp + total_fp) or 0
    recall = ratio(total_tp, total_tp + total_fn) or 0
    accuracy = sum(j["assertions"]["correct"]["value"] for j in judgments) / len(judgments)
    p95 = sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1]
    cost = (
        sum(c for c in costs if c is not None)
        if usage_complete and all(c is not None for c in costs)
        else None
    )
    safety = {
        "no_provider_errors": errors == 0,
        "no_false_mandates": metrics["false_mandatory_requirements"] == 0,
        "exact_grounding": metrics["evidence_grounding"] == 1,
        "no_false_equivalence": not any(j.get("false_equivalence", False) for j in judgments),
    }
    quality = {
        "precision": precision >= gates.minimum_precision,
        "recall": recall >= gates.minimum_recall,
        "scope": (metrics["scope_accuracy"] or 0) >= gates.minimum_scope_accuracy,
        "modality": (metrics["modality_accuracy"] or 0) >= gates.minimum_scope_accuracy,
        "semantic_pairs": accuracy >= gates.minimum_pair_accuracy,
        "retrieval_recall": retrieval_report["recall_at_k"] >= gates.minimum_retrieval_recall,
        "retrieval_mrr": retrieval_report["mrr"] >= gates.minimum_mrr,
        "latency": p95 <= gates.maximum_p95_ms,
        "embedding_latency": retrieval_report["latency_ms"] <= gates.maximum_p95_ms,
        "tokens": tokens <= gates.maximum_tokens,
        "metered_usage": usage_complete,
        "priced_cost": cost is not None and cost <= gates.maximum_estimated_cost,
    }
    passed = all(safety.values()) and (not live or all(quality.values()))
    report = {
        "recorded_at": now().isoformat(),
        "dataset_version": dataset.version,
        "dataset_sha256": content_hash(await asyncio.to_thread(dataset_path.read_bytes)),
        "gates": gates.model_dump(mode="json"),
        "teacher_model": dataset.teacher_model,
        "label_review": dataset.review_status,
        "student_model": inference.capabilities.model_id,
        "prompt_version": inference.prompt_version,
        "evaluation_runner": "pydantic-evals",
        "evaluation_runner_version": version("pydantic-evals"),
        "split": split,
        "live": live,
        "passed": passed,
        "student_meets_provisional_gates": live and passed and split == "test",
        "safety_gates": safety,
        "student_gates": quality,
        "precision": precision,
        "recall": recall,
        "semantic_accuracy": accuracy,
        "retrieval": retrieval_report,
        "p95_ms": p95,
        "total_tokens": tokens,
        "usage_complete": usage_complete,
        "estimated_cost": cost,
        "verifier_calls": calls,
        "extraction_cases": len(cases),
        "semantic_cases": len(pairs),
        "judgments": judgments,
        "limitations": [
            "Teacher-only labels are provisional, not independent human gold.",
            "Offline baselines never qualify a live student or embedding deployment.",
            "Sources/families do not cross splits; this small corpus limits generalization.",
            "Costs use provider amounts or supplied token prices; estimates are not invoices.",
            "Case budgets bound examples, not spend; retries may add requests.",
            "Latency/token/cost gates are post-run checks, not hard spending caps.",
            "Code tests separately evaluate ingestion, governance, resolution and issue lifecycle.",
        ],
    }
    await write_json(output, report)
    return report
