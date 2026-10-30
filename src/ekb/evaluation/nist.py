from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from ekb.adapters.parsers.csf import CsfNode, CsfPassage
from ekb.domain.models import (
    Candidate,
    InferenceResult,
    Model,
    Scope,
    Segment,
    SemanticType,
    SourceItem,
    SourceRevision,
    Usage,
    content_hash,
    fingerprint,
    stable_id,
    total_usage,
)
from ekb.domain.skills import validate_labeling, validate_scoped_extraction
from ekb.evaluation.dataset import write_json
from ekb.observability import tracer
from ekb.ports import DocumentParser, IngestionSkills

REFERENCE = Path("evals") / "nist-csf"


class NativeIdentity(Model):
    id: str
    parent_id: str | None = None
    label: str | None = None


class CaseEvidence(Model):
    printed_page: int | None = None
    pdf_page: int | None = None
    excerpt: str | None = None
    passages: tuple[CsfPassage, ...] = ()


class ReferenceCase(Model):
    id: str
    expected_type: SemanticType
    normalized_statement: str
    evidence: CaseEvidence


class NegativeCase(Model):
    id: str
    candidate: str
    expected: Literal["reject"]
    reason: str


class SourceExcerpts(Model):
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    nodes: tuple[CsfNode, ...]


class LocationCorrection(Model):
    id: str
    supplied_pdf_page: int
    resolved_pdf_pages: tuple[int, ...]


class NistReference(Model):
    source: SourceExcerpts
    functions: tuple[NativeIdentity, ...]
    categories: tuple[NativeIdentity, ...]
    subcategories: tuple[NativeIdentity, ...]
    cases: tuple[ReferenceCase, ...]
    negatives: tuple[NegativeCase, ...]
    location_corrections: tuple[LocationCorrection, ...] = ()


class SemanticPrediction(Model):
    id: str
    ontology_class: SemanticType | None = None
    authority: str | None = None
    enterprise_adopted: bool | Literal["unknown"] | None = None
    evidence: CaseEvidence = Field(default_factory=CaseEvidence)
    normalized_statement: str = ""
    scope: Scope = Field(default_factory=Scope)
    candidate: Candidate | None = None


class NegativeDecision(Model):
    id: str
    decision: Literal["reject", "accept", "unknown"]


class NistPredictions(Model):
    functions: tuple[NativeIdentity, ...] = ()
    categories: tuple[NativeIdentity, ...] = ()
    subcategories: tuple[NativeIdentity, ...] = ()
    semantic_cases: tuple[SemanticPrediction, ...] = ()
    negative_case_decisions: tuple[NegativeDecision, ...] = ()


def load_nist_reference(directory: Path = REFERENCE) -> NistReference:
    core = json.loads((directory / "csf_core_structure.json").read_bytes())
    semantic = json.loads((directory / "ekb_classification_reference.json").read_bytes())
    negatives = json.loads((directory / "negative_cases.json").read_bytes())
    source = SourceExcerpts.model_validate_json((directory / "source-excerpts.json").read_bytes())
    node_locations = {
        node.id: tuple(passage.pdf_page for passage in node.passages) for node in source.nodes
    }
    reference = NistReference(
        source=source,
        **{
            kind: tuple(
                NativeIdentity(
                    id=item["id"], parent_id=item.get("parent_id"), label=item.get("label")
                )
                for item in core[kind]
            )
            for kind in ("functions", "categories", "subcategories")
        },
        cases=tuple(
            ReferenceCase(
                id=item["id"],
                expected_type=item["expected_ekb_class"],
                normalized_statement=item["normalized_statement"],
                evidence=CaseEvidence(
                    printed_page=item["evidence"]["printed_page"],
                    pdf_page=item["evidence"]["pdf_page"],
                ),
            )
            for item in semantic["cases"]
        ),
        negatives=tuple(NegativeCase.model_validate(item) for item in negatives["cases"]),
        location_corrections=tuple(
            LocationCorrection(
                id=item["id"],
                supplied_pdf_page=item["evidence"]["pdf_page"],
                resolved_pdf_pages=node_locations.get(item["id"], ()),
            )
            for kind in ("categories", "subcategories")
            for item in core[kind]
            if item["evidence"].get("pdf_page") not in node_locations.get(item["id"], ())
        ),
    )
    ids = {node.id for node in source.nodes}
    expected = {
        node.id
        for group in (reference.functions, reference.categories, reference.subcategories)
        for node in group
    }
    if (
        ids != expected
        or len(ids) != len(source.nodes)
        or len(expected)
        != sum(
            len(group)
            for group in (reference.functions, reference.categories, reference.subcategories)
        )
    ):
        raise ValueError("Source excerpts must resolve every unique supplied native identity")
    if not {case.id for case in reference.cases} <= ids:
        raise ValueError("Curated cases must resolve to source identities")
    if len({case.id for case in reference.cases}) != len(reference.cases):
        raise ValueError("Curated case IDs must be unique")
    for case in reference.cases:
        if not any(
            case.evidence.pdf_page == passage.pdf_page
            and case.evidence.printed_page == passage.printed_page
            for node in source.nodes
            if node.id == case.id
            for passage in node.passages
        ):
            raise ValueError("Curated reference location does not match the retained source")
    return reference


def _counts(expected: set[str], actual: list[str]) -> dict:
    observed = set(actual)
    duplicates = len(actual) - len(observed)
    tp, fp, fn = (
        len(expected & observed),
        len(observed - expected) + duplicates,
        len(expected - observed),
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "duplicates": duplicates,
        "missing": sorted(expected - observed),
        "unexpected": sorted(observed - expected),
    }


def _verified_evidence(item: SemanticPrediction, node: CsfNode) -> bool:
    evidence = item.evidence
    if not evidence.excerpt:
        return False
    if evidence.passages:
        location_matches = any(
            (evidence.pdf_page is None or evidence.pdf_page == passage.pdf_page)
            and (evidence.printed_page is None or evidence.printed_page == passage.printed_page)
            for passage in evidence.passages
        )
        return (
            location_matches
            and all(passage in node.passages for passage in evidence.passages)
            and evidence.excerpt in " ".join(passage.excerpt for passage in evidence.passages)
        )
    return any(
        evidence.pdf_page == passage.pdf_page
        and evidence.printed_page == passage.printed_page
        and evidence.excerpt in passage.excerpt
        for passage in node.passages
    )


def score_nist(reference: NistReference, predictions: NistPredictions) -> dict:
    identity = {
        kind: _counts(
            {item.id for item in getattr(reference, kind)},
            [item.id for item in getattr(predictions, kind)],
        )
        for kind in ("functions", "categories", "subcategories")
    }
    expected_parents = {
        item.id: item.parent_id for item in (*reference.categories, *reference.subcategories)
    }
    actual_parents = [*predictions.categories, *predictions.subcategories]
    correct_parents = {
        item.id
        for item in actual_parents
        if item.id in expected_parents and item.parent_id == expected_parents[item.id]
    }
    nodes = {node.id: node for node in reference.source.nodes}
    rows = []
    for case in reference.cases:
        observed = [item for item in predictions.semantic_cases if item.id == case.id]
        verified = [item for item in observed if _verified_evidence(item, nodes[case.id])]
        rows.append(
            {
                "id": case.id,
                "expected_class": case.expected_type,
                "predicted_classes": [item.ontology_class for item in observed],
                "identified": bool(observed),
                "class_correct": any(
                    item.ontology_class == case.expected_type for item in observed
                ),
                "provenance_verified": bool(verified),
                "missing": not observed,
                "duplicates": max(0, len(observed) - 1),
            }
        )
    decisions: dict[str, list[str]] = {}
    for item in predictions.negative_case_decisions:
        decisions.setdefault(item.id, []).append(item.decision)
    negatives = [
        {
            "id": case.id,
            "claim": case.candidate,
            "reason": case.reason,
            "result": (
                "not_run"
                if case.id not in decisions
                else "pass"
                if decisions[case.id] == ["reject"]
                else "fail"
            ),
        }
        for case in reference.negatives
    ]
    scope_errors = [item.id for item in predictions.semantic_cases if item.scope != Scope()]
    adoption_errors = [
        item.id for item in predictions.semantic_cases if item.enterprise_adopted is True
    ]
    authority_errors = [
        item.id
        for item in predictions.semantic_cases
        if item.authority not in (None, "external_nonprescriptive_guidance")
    ]
    taxonomy_goals = [
        item.id
        for item in predictions.semantic_cases
        if item.ontology_class in {SemanticType.STRATEGIC_GOAL, SemanticType.PLATFORM_REQUIREMENT}
    ]
    strategic_candidates = [
        item.id
        for item in predictions.semantic_cases
        if item.ontology_class == SemanticType.STRATEGIC_REQUIREMENT
    ]
    unsupported_numbers = [
        item.id
        for item in predictions.semantic_cases
        if set(re.findall(r"\b\d+(?:[.,]\d+)*%?", item.normalized_statement))
        - set(re.findall(r"\b\d+(?:[.,]\d+)*%?", item.evidence.excerpt or ""))
    ]
    return {
        "identity_metrics": identity,
        "hierarchy": {
            "expected_links": len(expected_parents),
            "correct_links": len(correct_parents),
            "missing_or_wrong_links": sorted(set(expected_parents) - correct_parents),
            "incorrect_parent_entries": [
                item.id
                for item in actual_parents
                if item.id in expected_parents and item.parent_id != expected_parents[item.id]
            ],
        },
        "curated_semantics": {
            "expected": len(rows),
            "identified": sum(row["identified"] for row in rows),
            "correct_classes": sum(row["class_correct"] for row in rows),
            "verified_provenance": sum(row["provenance_verified"] for row in rows),
            "unexpected_ids": sorted(
                {item.id for item in predictions.semantic_cases} - {row["id"] for row in rows}
            ),
            "cases": rows,
            "semantic_fidelity": "requires_independent_review",
        },
        "authority_and_scope": {
            "invented_scope": scope_errors,
            "invented_adoption": adoption_errors,
            "unsupported_authority": authority_errors,
            "unsupported_goal_or_platform": taxonomy_goals,
            "strategic_requirement_candidates": strategic_candidates,
            "invented_numeric_qualifiers": unsupported_numbers,
            "authority_origin": "Source-context fields are independent of ontology classification.",
        },
        "negative_cases": negatives,
        "unexpected_negative_decision_ids": sorted(
            set(decisions) - {case.id for case in reference.negatives}
        ),
        "reference_location_corrections": [
            item.model_dump(mode="json") for item in reference.location_corrections
        ],
        "reference_disagreements": [
            {
                "subject": "StrategicRequirement versus adoption",
                "supplied_reference": "Prohibits StrategicRequirement from NIST alone.",
                "application_policy": (
                    "User retains strategic requirement candidates separately from adoption; "
                    "no blanket type violation."
                ),
            },
            {
                "subject": "PR.DS-01",
                "supplied_reference": "TechnicalRequirement",
                "earlier_document_reference": "ConceptualRequirement",
                "treatment": (
                    "Both provisional labels retained; "
                    "score the supplied class without rewriting it."
                ),
            },
        ],
    }


def _native_predictions(nodes: tuple[CsfNode, ...]) -> dict[str, tuple[NativeIdentity, ...]]:
    return {
        kind: tuple(
            NativeIdentity(id=node.id, parent_id=node.parent_id, label=node.label)
            for node in nodes
            if node.level == level
        )
        for kind, level in (
            ("functions", "Function"),
            ("categories", "Category"),
            ("subcategories", "Subcategory"),
        )
    }


def _markdown(report: dict) -> str:
    metrics = report["metrics"]
    lines = [
        "# NIST CSF 2.0 extraction evaluation",
        "",
        f"- Run: `{report['run_id']}`; status: **{report['status']}**",
        f"- Source mode: `{report['source_mode']}`; model: `{report['model_id']}`",
        f"- Current usage: {report['current_usage']}; cache hits: {report['cache_hits']}",
        f"- Elapsed milliseconds: {report['elapsed_ms']:.1f}",
        "- Reference mappings are provisional, not independently reviewed gold.",
        "- Strategic requirement candidates do not establish enterprise adoption.",
        "",
        "| Native level | TP | FP | FN |",
        "|---|---:|---:|---:|",
    ]
    for kind, value in metrics["identity_metrics"].items():
        lines.append(f"| {kind} | {value['tp']} | {value['fp']} | {value['fn']} |")
    semantic = metrics["curated_semantics"]
    negative_counts = dict(Counter(item["result"] for item in metrics["negative_cases"]))
    location_corrections = len(metrics["reference_location_corrections"])
    lines.extend(
        [
            "",
            f"Curated outcomes identified: {semantic['identified']}/{semantic['expected']}; "
            f"correct provisional classes: {semantic['correct_classes']}/{semantic['expected']}; "
            f"source-verified evidence: {semantic['verified_provenance']}/{semantic['expected']}.",
            f"Negative decisions: {negative_counts}.",
            f"Supplied structural page locators corrected from source: {location_corrections}.",
            "",
            "| Outcome | Expected class | Predicted classes | Verified evidence |",
            "|---|---|---|---|",
        ]
    )
    for row in semantic["cases"]:
        classes = ", ".join(value or "unknown" for value in row["predicted_classes"]) or "missing"
        lines.append(
            f"| {row['id']} | {row['expected_class']} | {classes} | {row['provenance_verified']} |"
        )
    lines.extend(["", "## Reference disagreements", ""])
    lines.extend(json.dumps(item, ensure_ascii=True) for item in metrics["reference_disagreements"])
    lines.extend(["", "## Limitations", "", *report["limitations"]])
    return "\n".join(lines) + "\n"


async def evaluate_nist(
    source: Path | None,
    output: Path,
    parser: DocumentParser,
    inference: IngestionSkills,
    *,
    reference_dir: Path = REFERENCE,
    imported_predictions: Path | None = None,
    live: bool = False,
    max_model_calls: int = 500,
    calls_per_case: int = 18,
) -> dict:
    started = time.monotonic()
    reference = await asyncio.to_thread(load_nist_reference, reference_dir)
    cache_dir = output / "cache"
    await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)
    nodes = reference.source.nodes
    source_mode = "committed_source_excerpts"
    parser_cache_hit = False
    if source is not None:
        raw = await asyncio.to_thread(source.read_bytes)
        if content_hash(raw) != reference.source.source_sha256:
            raise ValueError("NIST source hash does not match the retained reference publication")
        source_mode = "retained_pdf"
        key = fingerprint(source=content_hash(raw), parser=parser.version)
        path = cache_dir / f"parse-{key}.json"
        if await asyncio.to_thread(path.exists):
            cached = SourceExcerpts.model_validate_json(await asyncio.to_thread(path.read_bytes))
            nodes = cached.nodes
            parser_cache_hit = True
        else:
            item = SourceItem(
                id=stable_id("NIST.CSWP.29"),
                connector="evaluation",
                location=str(source),
                name="NIST CSF 2.0",
                format="pdf",
                metadata={"framework_namespace": "nist-csf"},
            )
            revision = SourceRevision(
                id=stable_id(item.id, content_hash(raw)),
                source_id=item.id,
                content_hash=content_hash(raw),
                artifact_key=str(source),
            )
            segments = await parser.parse(item, revision, raw)
            if not all("csf_node" in segment.location for segment in segments):
                raise ValueError("Configured parser does not expose native CSF identity/evidence")
            nodes = tuple(
                CsfNode.model_validate(segment.location["csf_node"]) for segment in segments
            )
            await write_json(
                path,
                SourceExcerpts(source_sha256=content_hash(raw), nodes=nodes).model_dump(
                    mode="json"
                ),
            )
    semantic: list[SemanticPrediction] = []
    errors: dict[str, str] = {}
    usages: list[Usage] = []
    cache_hits = budget = 0
    if imported_predictions:
        predictions = NistPredictions.model_validate_json(
            await asyncio.to_thread(imported_predictions.read_bytes)
        )
    else:
        wanted = {case.id for case in reference.cases}
        for node in nodes:
            if node.id not in wanted:
                continue
            segment = Segment(
                id=stable_id(reference.source.source_sha256, node.id, node.text),
                revision_id=stable_id(reference.source.source_sha256),
                ordinal=0,
                text=node.text,
                location={"structured_control": True, "control_id": node.id, "part_id": node.id},
            )
            key = fingerprint(
                text=segment.text,
                source=reference.source.source_sha256,
                labeling=inference.label_fingerprint,
                extraction=inference.extraction_fingerprint,
            )
            path = cache_dir / f"extraction-{key}.json"
            call_started = charged = False
            try:
                if await asyncio.to_thread(path.exists):
                    result = InferenceResult.model_validate_json(
                        await asyncio.to_thread(path.read_bytes)
                    )
                    cache_hits += 1
                else:
                    offline = inference.capabilities.model_id.startswith("offline-")
                    reserve = 0 if offline else calls_per_case
                    if budget + reserve > max_model_calls:
                        raise ValueError("NIST evaluation model-call budget exceeded")
                    call_started = True
                    with tracer.start_as_current_span("evaluation.nist.outcome"):
                        labeling = await inference.label(segment)
                        validate_labeling(labeling.labeling, segment)
                        extracted = await inference.extract_scoped(segment, labeling.labeling)
                    result = extracted.model_copy(
                        update={
                            "labeling": labeling,
                            "usage": total_usage([labeling.usage, extracted.usage]),
                        }
                    )
                    budget += result.usage.model_calls
                    charged = True
                    usages.append(result.usage)
                    await write_json(path, result.model_dump(mode="json"))
                if result.labeling is None:
                    raise ValueError("NIST extraction cache lacks independent labeling")
                validate_scoped_extraction(result.extraction, segment, result.labeling.labeling)
                for candidate in result.extraction.candidates:
                    passage = next(
                        (part for part in node.passages if candidate.excerpt in part.excerpt), None
                    )
                    semantic.append(
                        SemanticPrediction(
                            id=node.id,
                            ontology_class=candidate.semantic_type,
                            authority="external_nonprescriptive_guidance",
                            enterprise_adopted=None,
                            evidence=CaseEvidence(
                                printed_page=passage.printed_page if passage else None,
                                pdf_page=passage.pdf_page if passage else None,
                                excerpt=candidate.excerpt,
                                passages=() if passage else node.passages,
                            ),
                            normalized_statement=candidate.statement,
                            scope=candidate.scope,
                            candidate=candidate,
                        )
                    )
            except Exception as error:
                errors[node.id] = type(error).__name__
                if (
                    call_started
                    and not charged
                    and not inference.capabilities.model_id.startswith("offline-")
                ):
                    budget += calls_per_case
        native = _native_predictions(nodes)
        predictions = NistPredictions(
            functions=native["functions"],
            categories=native["categories"],
            subcategories=native["subcategories"],
            semantic_cases=tuple(semantic),
        )
    metrics = score_nist(reference, predictions)
    usage = total_usage(usages)
    native_ok = all(
        value["fp"] == value["fn"] == 0 for value in metrics["identity_metrics"].values()
    ) and not (
        metrics["hierarchy"]["missing_or_wrong_links"]
        or metrics["hierarchy"]["incorrect_parent_entries"]
    )
    safety = metrics["authority_and_scope"]
    safe = (
        not errors
        and native_ok
        and not any(item["result"] == "fail" for item in metrics["negative_cases"])
        and not metrics["unexpected_negative_decision_ids"]
        and not any(
            safety[key]
            for key in (
                "invented_scope",
                "invented_adoption",
                "unsupported_authority",
                "unsupported_goal_or_platform",
                "invented_numeric_qualifiers",
            )
        )
    )
    semantic_ok = (
        metrics["curated_semantics"]["correct_classes"]
        == metrics["curated_semantics"]["verified_provenance"]
        == len(reference.cases)
    ) and not (
        metrics["curated_semantics"]["unexpected_ids"]
        or any(row["duplicates"] for row in metrics["curated_semantics"]["cases"])
    )
    extraction_ok = (
        metrics["curated_semantics"]["identified"]
        == metrics["curated_semantics"]["verified_provenance"]
        == len(reference.cases)
    ) and not (
        metrics["curated_semantics"]["unexpected_ids"]
        or any(row["duplicates"] for row in metrics["curated_semantics"]["cases"])
    )
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    report = {
        "schema_version": "nist-evaluation-v1",
        "run_id": run_id,
        "status": "incomplete" if errors else "completed",
        "passed": safe and extraction_ok and (not live or semantic_ok),
        "qualified": False,
        "source_mode": source_mode,
        "source_sha256": reference.source.source_sha256,
        "reference_fingerprint": fingerprint(reference=reference.model_dump(mode="json")),
        "parser_version": parser.version,
        "parser_cache_hit": parser_cache_hit,
        "model_id": inference.capabilities.model_id
        if imported_predictions is None
        else "provided_predictions",
        "label_fingerprint": inference.label_fingerprint,
        "extraction_fingerprint": inference.extraction_fingerprint,
        "live": live,
        "cache_hits": cache_hits,
        "elapsed_ms": (time.monotonic() - started) * 1000,
        "current_usage": usage.model_dump(mode="json"),
        "model_call_budget_consumed": budget,
        "model_call_count_complete": not errors,
        "errors": errors,
        "metrics": metrics,
        "native_source_nodes": [node.model_dump(mode="json") for node in nodes],
        "predictions": predictions.model_dump(mode="json"),
        "limitations": [
            "Provisional mappings and statements require independent fidelity review.",
            "Only 25 curated source outcomes receive semantic extraction; "
            "no expected labels in prompts.",
            "Missing negative decisions are NOT RUN, never automatically counted as passed.",
            "Excerpt replay is not a new source audit; retained_pdf uses fingerprinted parsing.",
            "Offline rules preserve outcomes/identity but do not qualify ontology classification.",
            "No enterprise adoption is inferred from source taxonomy, ontology class or publisher.",
        ],
    }
    json_path = output / f"nist-evaluation-{run_id}.json"
    previous_paths = await asyncio.to_thread(
        lambda: sorted(output.glob("nist-evaluation-*.json"), key=lambda path: path.name)
    )
    for previous in reversed(previous_paths):
        prior = json.loads(await asyncio.to_thread(previous.read_bytes))
        if (
            prior.get("source_sha256") == report["source_sha256"]
            and prior.get("reference_fingerprint") == report["reference_fingerprint"]
            and prior.get("model_id") == report["model_id"]
            and prior.get("source_mode") == report["source_mode"]
        ):
            report["previous_run"] = previous.name
            report["correct_class_delta"] = (
                metrics["curated_semantics"]["correct_classes"]
                - prior["metrics"]["curated_semantics"]["correct_classes"]
            )
            break
    markdown_path = json_path.with_suffix(".md")
    await write_json(json_path, report)
    await asyncio.to_thread(markdown_path.write_text, _markdown(report), encoding="utf-8")
    return {**report, "json_report": str(json_path), "markdown_report": str(markdown_path)}
