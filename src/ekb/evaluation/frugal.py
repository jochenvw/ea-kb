from __future__ import annotations

import asyncio
import math
import time
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import Field

from ekb.domain.models import Model, content_hash, now
from ekb.domain.reconciliation import structural_verification
from ekb.evaluation import evaluate, ratio
from ekb.evaluation.benchmarks import evaluate_benchmarks
from ekb.evaluation.dataset import (
    Split,
    TeacherDataset,
    extraction_cases,
    load_dataset,
    pair_cases,
    write_json,
)
from ekb.ports import EmbeddingPort, InferencePort, SemanticVerifier
from ekb.services.retrieval import nearest


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


async def retrieval(
    dataset: TeacherDataset,
    split: Split,
    k: int,
    embeddings: EmbeddingPort | None,
) -> dict:
    seeds = dataset.selected(split)
    texts = list(dict.fromkeys([s.quote for s in seeds] + [s.query for s in seeds]))
    vectors = {}
    calls = tokens = 0
    started = time.monotonic()
    if embeddings:
        result = await embeddings.embed(texts)
        if len(result.vectors) != len(texts):
            raise ValueError("Embedding provider omitted requested vectors")
        vectors = dict(zip(texts, result.vectors, strict=True))
        calls, tokens = result.calls, result.input_tokens
    rows = []
    for seed in seeds:
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
                    (s.id, SequenceMatcher(None, seed.query.casefold(), s.quote.casefold()).ratio())
                    for s in seeds
                ),
                key=lambda x: (-x[1], x[0]),
            )[:k]
        # Both published WAF editions answer WAF queries; a native mapping is not equivalence.
        relevant = {"ns1-waf", "ns2-waf"} if seed.id in {"ns1-waf", "ns2-waf"} else {seed.id}
        ids = [key for key, _ in ranked]
        found = relevant.intersection(ids)
        rank = next((i + 1 for i, key in enumerate(ids) if key in relevant), None)
        rows.append(
            {
                "id": seed.id,
                "expected": sorted(relevant),
                "retrieved": ids,
                "recall": len(found) / len(relevant),
                "reciprocal_rank": 1 / rank if rank else 0,
            }
        )
    return {
        "provider": embeddings.fingerprint if embeddings else "lexical-rules",
        "model_id": embeddings.model_id if embeddings else None,
        "recall_at_k": sum(r["recall"] for r in rows) / len(rows),
        "mrr": sum(r["reciprocal_rank"] for r in rows) / len(rows),
        "k": k,
        "queries": rows,
        "calls": calls,
        "input_tokens": tokens,
        "latency_ms": (time.monotonic() - started) * 1000,
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
    for identifier, old, new, expected in pairs:
        started = time.monotonic()
        try:
            baseline = structural_verification(old, new)
            result = (
                await verifier.verify(old, new)
                if verifier
                and baseline.judgment.outcome != "exact_duplicate"
                and old.scope == new.scope
                else baseline
            )
            grounded = (
                bool(result.judgment.old_excerpt.strip())
                and bool(result.judgment.new_excerpt.strip())
                and result.judgment.old_excerpt in old.control.evidence.excerpt
                and result.judgment.new_excerpt in new.control.evidence.excerpt
            )
            valid = grounded and result.method in {"small_model", "structural_rules"}
            actual = result.judgment.outcome
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
                    "actual": actual,
                    "valid": valid,
                    "false_equivalence": actual in {"equivalent", "exact_duplicate"}
                    and expected not in {"equivalent", "exact_duplicate"},
                    "result": result.model_dump(mode="json"),
                }
            )
        except Exception as error:
            errors += 1
            usage_complete = False
            judgments.append(
                {
                    "id": identifier,
                    "expected": expected,
                    "error": type(error).__name__,
                    "valid": False,
                }
            )
        latencies.append((time.monotonic() - started) * 1000)
    retrieval_report = await retrieval(dataset, split, gates.retrieval_k, embeddings)
    tokens += retrieval_report["input_tokens"]
    if embeddings:
        usage_complete &= retrieval_report["input_tokens"] > 0
        costs.append(
            retrieval_report["input_tokens"] * embedding_input_price / 1_000_000
            if embedding_input_price is not None
            else None
        )
    benchmark = await evaluate_benchmarks(
        Path("tests") / "golden" / "benchmarks.json",
        output.with_name(output.stem + "-benchmarks.json"),
    )
    metrics = extraction["metrics"]
    categories = [value for value in metrics.values() if isinstance(value, dict) and "tp" in value]
    total_tp = sum(value["tp"] for value in categories)
    total_fp = sum(value["fp"] for value in categories)
    total_fn = sum(value["fn"] for value in categories)
    precision = ratio(total_tp, total_tp + total_fp) or 0
    recall = ratio(total_tp, total_tp + total_fn) or 0
    accuracy = sum(j["valid"] and j.get("actual") == j["expected"] for j in judgments) / len(
        judgments
    )
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
        "parser_controls": all(
            benchmark["metrics"]["extraction"]["controls"][key] == 1
            for key in ("precision", "recall")
        ),
        "parser_mappings": all(
            benchmark["metrics"]["extraction"]["mappings"][key] == 1
            for key in ("precision", "recall")
        ),
        "parser_maturity": benchmark["metrics"]["extraction"]["publication_accuracy"] == 1,
        "policy_metadata": benchmark["metrics"]["extraction"]["policy_accuracy"] == 1,
        "implementation_alternatives": benchmark["metrics"]["extraction"]["alternatives_accuracy"]
        == 1,
        "exact_benchmark_evidence": benchmark["metrics"]["lineage"]["exact_passage_grounding"] == 1,
        "published_lineage": benchmark["metrics"]["lineage"]["published_succession"]["recall"] == 1,
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
            "Costs require supplied per-million-token prices and are estimates, not invoices.",
            "Case budgets bound examples, not spend; retries may add requests.",
            "Latency/token/cost gates are post-run checks, not hard spending caps.",
            "Code tests separately evaluate ingestion, governance, resolution and issue lifecycle.",
        ],
    }
    await write_json(output, report)
    return report
