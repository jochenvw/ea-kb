from __future__ import annotations

import asyncio
import json
from collections.abc import Hashable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ekb.adapters.parsers.benchmarks import BENCHMARK_PARSER_VERSION, normalize_benchmark
from ekb.adapters.sources.benchmarks import EDITIONS, _asset
from ekb.domain.models import Model, PublicationStatus, content_hash, now, stable_id
from ekb.domain.reconciliation import (
    ChangeClass,
    ControlView,
    SemanticOutcome,
    structural_verification,
)
from ekb.evaluation import ratio
from ekb.ports import SemanticVerifier


class ExpectedControl(Model):
    control_id: str
    title: str
    security_principle: str | None
    subcontrols: tuple[str, ...] = ()
    mappings: tuple[tuple[str, str | None, str], ...]
    alternatives: tuple[str, ...] = ()
    explicit_alternatives: bool | None = None


class ExpectedPolicy(Model):
    definition_id: str
    version: str
    mode: str
    resource_types: tuple[str, ...]


class BenchmarkCase(Model):
    id: str
    edition: str
    role: Literal["domain", "overview", "policy"]
    source_url: str
    original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    excerpt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_excerpt: str
    attribution: str
    license: str
    expected_control: ExpectedControl | None = None
    expected_successors: tuple[tuple[str, str], ...] = ()
    expected_policy: ExpectedPolicy | None = None
    expected_publication: PublicationStatus = PublicationStatus.UNKNOWN


class ReconciliationCase(Model):
    old: str
    new: str
    expected_outcome: SemanticOutcome
    expected_change: ChangeClass | None
    expected_mapping_change: bool
    rationale: str


class BenchmarkGolden(Model):
    description: str
    cases: tuple[BenchmarkCase, ...]
    reconciliation: tuple[ReconciliationCase, ...]


def precision_recall[T: Hashable](
    expected: set[T], actual: set[T]
) -> dict[str, int | float | None]:
    tp, fp, fn = len(expected & actual), len(actual - expected), len(expected - actual)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
    }


async def evaluate_benchmarks(
    dataset: Path, output: Path, verifier: SemanticVerifier | None = None
) -> dict[str, Any]:
    raw = await asyncio.to_thread(dataset.read_bytes)
    golden = BenchmarkGolden.model_validate_json(raw)
    views = {}
    predictions = []
    expected_controls, actual_controls = set(), set()
    expected_mappings, actual_mappings = set(), set()
    expected_lineage, actual_lineage = set(), set()
    principles = options = subcontrols = maturity = policies = policy_count = 0
    grounded = passages_total = 0
    for case in golden.cases:
        content = case.source_excerpt.encode("utf-8")
        if content_hash(content) != case.excerpt_sha256:
            raise ValueError(f"Golden excerpt integrity failure: {case.id}")
        asset = _asset(
            case.edition,
            case.source_url,
            case.role,
            definition_id=case.expected_policy.definition_id if case.expected_policy else None,
        )
        document = await asyncio.to_thread(
            normalize_benchmark, asset, content, EDITIONS[case.edition]
        )
        expected = case.expected_control
        if expected:
            expected_controls.add((case.id, expected.control_id))
            expected_mappings.update((case.id, *m) for m in expected.mappings)
            actual_controls.update((case.id, c.control_id) for c in document.controls)
            for control in document.controls:
                actual_mappings.update(
                    (case.id, m.framework, m.revision, m.control_id) for m in control.mappings
                )
            matching = next(
                (c for c in document.controls if c.control_id == expected.control_id), None
            )
            if matching:
                principles += matching.security_principle == expected.security_principle
                options += (
                    matching.implementation_options == expected.alternatives
                    and matching.implementation_options_are_alternatives
                    == expected.explicit_alternatives
                )
                subcontrols += (
                    tuple(c.control_id for c in matching.subcontrols) == expected.subcontrols
                )
                views[case.id] = ControlView(
                    snapshot_id=stable_id("golden-case", case.id),
                    revision_id=stable_id("golden-excerpt", case.excerpt_sha256),
                    segment_id=document.segment_id,
                    retrieved_at=now(),
                    control=matching,
                )
        expected_lineage.update((case.id, *edge) for edge in case.expected_successors)
        actual_lineage.update((case.id, e.successor, e.predecessor) for e in document.successors)
        maturity += document.publication.publication_status == case.expected_publication
        if case.expected_policy:
            policy_count += 1
            wanted = case.expected_policy
            policies += any(
                (p.definition_id, p.version, p.mode, p.resource_types)
                == (wanted.definition_id, wanted.version, wanted.mode, wanted.resource_types)
                for p in document.policies
            )
        passages = [
            p
            for c in document.controls
            for p in (
                c.evidence,
                *([c.principle_evidence] if c.principle_evidence else []),
                *(m.evidence for m in c.mappings),
                *(s.evidence for s in c.subcontrols),
            )
        ]
        passages.extend(e.evidence for e in document.successors)
        grounded += sum(document.text[p.start : p.end] == p.excerpt for p in passages)
        passages_total += len(passages)
        predictions.append(
            {
                "id": case.id,
                "source_url": case.source_url,
                "original_sha256": case.original_sha256,
                "excerpt_sha256": case.excerpt_sha256,
                "controls": [c.model_dump(mode="json") for c in document.controls],
                "successors": [e.model_dump(mode="json") for e in document.successors],
                "publication": document.publication.model_dump(mode="json"),
                "policies": [p.model_dump(mode="json") for p in document.policies],
            }
        )
    judgments = []
    expected_outcomes, actual_outcomes = set(), set()
    expected_equivalence, actual_equivalence = set(), set()
    equivalent_outcomes = {
        "exact_duplicate",
        "equivalent",
        "compatible_extension",
        "more_specific_guidance",
    }
    outcome_correct = change_correct = mapping_change_correct = calls = input_tokens = (
        output_tokens
    ) = 0
    for index, case in enumerate(golden.reconciliation):
        expected_outcomes.add((index, case.expected_outcome))
        if case.expected_outcome in equivalent_outcomes:
            expected_equivalence.add(index)
        old, new = views.get(case.old), views.get(case.new)
        if old is None or new is None:
            judgments.append({"old": case.old, "new": case.new, "error": "MissingExtractedControl"})
            continue
        baseline = structural_verification(old, new)
        result = (
            await verifier.verify(old, new)
            if verifier and baseline.judgment.outcome != "exact_duplicate"
            else baseline
        )
        if (
            result.judgment.old_excerpt not in old.control.evidence.excerpt
            or result.judgment.new_excerpt not in new.control.evidence.excerpt
        ):
            raise ValueError("Evaluation refuses ungrounded semantic evidence")
        actual_outcomes.add((index, result.judgment.outcome))
        if result.judgment.outcome in equivalent_outcomes:
            actual_equivalence.add(index)
        outcome_correct += result.judgment.outcome == case.expected_outcome
        change_correct += result.judgment.change_class == case.expected_change
        a = {(m.framework, m.revision, m.control_id) for m in old.control.mappings}
        b = {(m.framework, m.revision, m.control_id) for m in new.control.mappings}
        mapping_change_correct += (a != b) == case.expected_mapping_change
        calls += result.model_calls
        input_tokens += result.usage.input_tokens
        output_tokens += result.usage.output_tokens
        judgments.append(
            {"old": case.old, "new": case.new, "result": result.model_dump(mode="json")}
        )
    control_count = len(expected_controls)
    report = {
        "recorded_at": now().isoformat(),
        "dataset_sha256": content_hash(raw),
        "parser_version": BENCHMARK_PARSER_VERSION,
        "provider": verifier.fingerprint if verifier else "structural_rules",
        "offline_baseline": verifier is None,
        "metrics": {
            "extraction": {
                "controls": precision_recall(expected_controls, actual_controls),
                "mappings": precision_recall(expected_mappings, actual_mappings),
                "principle_accuracy": ratio(principles, control_count),
                "subcontrol_accuracy": ratio(subcontrols, control_count),
                "alternatives_accuracy": ratio(options, control_count),
                "publication_accuracy": ratio(maturity, len(golden.cases)),
                "policy_accuracy": ratio(policies, policy_count),
            },
            "reconciliation": {
                "outcomes": precision_recall(expected_outcomes, actual_outcomes),
                "equivalence_candidates": precision_recall(
                    expected_equivalence, actual_equivalence
                ),
                "outcome_accuracy": ratio(outcome_correct, len(golden.reconciliation)),
                "change_class_accuracy": ratio(change_correct, len(golden.reconciliation)),
                "changed_mappings_accuracy": ratio(
                    mapping_change_correct, len(golden.reconciliation)
                ),
                "cases": len(golden.reconciliation),
            },
            "lineage": {
                "published_succession": precision_recall(expected_lineage, actual_lineage),
                "exact_passage_grounding": ratio(grounded, passages_total),
            },
        },
        "model_calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "predictions": predictions,
        "reconciliation": judgments,
        "limitations": [
            "Small attributed excerpt dataset; not whole-benchmark quality or a compliance score.",
            "Original hashes are recorded; offline runs verify excerpt hashes, not originals.",
            "Structural proposals are not model-verified; no result adopts enterprise authority.",
            "Native resolution and idempotent ingestion have separate integration checks.",
            "Embedding retrieval quality is not measured by this small offline dataset.",
        ],
    }
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, json.dumps(report, indent=2), encoding="utf-8")
    return report
