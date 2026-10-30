from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pymupdf
from pydantic import Field, model_validator

from ekb.adapters.parsers import StructuredParser, split_text
from ekb.domain.grounding import validate_evidence
from ekb.domain.models import (
    Candidate,
    Extraction,
    GoalLevel,
    InferenceResult,
    Model,
    RelationshipKind,
    Scope,
    Segment,
    SemanticType,
    SourceItem,
    SourceRevision,
    Usage,
    content_hash,
    fingerprint,
    now,
    stable_id,
    total_usage,
)
from ekb.observability import tracer
from ekb.ports import GoalSynthesisPort, IngestionSkills

type GoalAbstraction = Literal[
    "enterprise_goal",
    "business_unit_goal",
    "subordinate_target",
    "capability",
    "initiative",
    "historical_fact",
    "descriptive_fact",
    "requirement",
    "external_context",
    "risk",
    "strategy_summary",
]
type GoalDisposition = Literal["primary", "acceptable_alternative", "negative_example"]
type GoalStrategy = Literal["whole_document", "section_aware", "hierarchical"]


class GoalEvidence(Model):
    excerpt: str = Field(min_length=1)
    printed_page: int = Field(gt=0)
    pdf_page: int = Field(gt=0)
    section: str
    strength: Literal["direct", "corroborating", "weak"]
    explicitness: Literal["explicit", "synthesized"]


class GoalAnnotation(Model):
    id: str
    expected_type: SemanticType | None
    abstraction: GoalAbstraction
    primary_or_alternative: GoalDisposition
    normalized_statement: str
    scope: Scope
    evidence: tuple[GoalEvidence, ...] = Field(min_length=1)
    rationale: str
    related_target_ids: tuple[str, ...] = ()
    ambiguity: str

    @model_validator(mode="after")
    def valid_reference_class(self) -> GoalAnnotation:
        if self.primary_or_alternative != "negative_example":
            if self.expected_type != SemanticType.STRATEGIC_GOAL:
                raise ValueError("Positive goal references must use the ontology StrategicGoal")
            if self.abstraction not in {"enterprise_goal", "business_unit_goal"}:
                raise ValueError("Included goal references must be enterprise or unit outcomes")
        return self


class GoalHypothesis(Model):
    from_annotation: str
    to_annotation: str
    property: Literal["supports", "refines"]
    strength: Literal["explicit", "synthesized"]
    note: str


class GoalSource(Model):
    id: str
    name: str
    filename: str
    url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_pages: int = Field(gt=0)
    printed_page_offset_note: str
    attribution: str


class GoalDataset(Model):
    version: str
    review_status: Literal["provisional_reference_annotations", "independently_reviewed"]
    annotation_note: str
    source: GoalSource
    section_aware_pdf_pages: tuple[int, ...] = Field(min_length=1)
    annotations: tuple[GoalAnnotation, ...] = Field(min_length=1)
    hierarchy_hypotheses: tuple[GoalHypothesis, ...] = ()

    @model_validator(mode="after")
    def valid_references(self) -> GoalDataset:
        ids = {annotation.id for annotation in self.annotations}
        if len(ids) != len(self.annotations):
            raise ValueError("Goal annotation IDs must be unique")
        if any(page < 1 or page > self.source.pdf_pages for page in self.section_aware_pdf_pages):
            raise ValueError("Section page is outside the PDF")
        for annotation in self.annotations:
            for evidence in annotation.evidence:
                if evidence.pdf_page > self.source.pdf_pages:
                    raise ValueError(f"{annotation.id} references a page outside the PDF")
                if evidence.pdf_page not in self.section_aware_pdf_pages:
                    raise ValueError(
                        f"{annotation.id} evidence is omitted from section-aware pages"
                    )
            if not set(annotation.related_target_ids) <= ids:
                raise ValueError(f"{annotation.id} refers to an unknown target annotation")
        for link in self.hierarchy_hypotheses:
            if link.from_annotation not in ids or link.to_annotation not in ids:
                raise ValueError("Hierarchy hypothesis refers to an unknown annotation")
        return self


class GoalPrediction(Model):
    candidate: Candidate
    pdf_page: int | None
    printed_page: int | None
    section: str | None
    annotation_matches: tuple[str, ...]
    verified_pdf_pages: tuple[int, ...]
    segment_id: str
    candidate_index: int


class GoalLinkPrediction(Model):
    from_annotation: str
    to_annotation: str
    property: Literal["supports", "refines"]


class GoalMetricContext(Model):
    dataset: GoalDataset
    predictions: tuple[GoalPrediction, ...]
    hierarchy_predictions: tuple[GoalLinkPrediction, ...] = ()


type GoalMetric = Callable[[GoalMetricContext], dict[str, Any]]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def load_goal_dataset(path: Path) -> GoalDataset:
    return GoalDataset.model_validate_json(path.read_bytes())


def _annotation_matches(candidate: Candidate, dataset: GoalDataset) -> tuple[str, ...]:
    excerpt = _norm(candidate.excerpt)
    if not excerpt:
        return ()
    matches = []
    for annotation in dataset.annotations:
        if any(
            (
                (anchor := _norm(evidence.excerpt))
                and min(len(anchor), len(excerpt)) >= 20
                and (anchor in excerpt or excerpt in anchor)
            )
            for evidence in annotation.evidence
        ):
            matches.append(annotation.id)
    return tuple(matches)


def _counts(tp: int, fp: int, fn: int) -> dict[str, Any]:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def detection_metric(context: GoalMetricContext) -> dict[str, Any]:
    predictions = [
        (index, prediction)
        for index, prediction in enumerate(context.predictions)
        if prediction.candidate.semantic_type == SemanticType.STRATEGIC_GOAL
        and prediction.candidate.goal_level != GoalLevel.BUSINESS_UNIT
        and prediction.candidate.goal_level != GoalLevel.SUBORDINATE_TARGET
    ]
    accepted: dict[str, int] = {}
    accepted_prediction_indices: set[int] = set()
    for prediction_index, prediction in predictions:
        eligible = [
            annotation_id
            for annotation_id in prediction.annotation_matches
            if prediction.candidate.goal_level == GoalLevel.ENTERPRISE
            and next(
                item for item in context.dataset.annotations if item.id == annotation_id
            ).primary_or_alternative
            in {"primary", "acceptable_alternative"}
        ]
        if len(eligible) == 1:
            accepted[eligible[0]] = accepted.get(eligible[0], 0) + 1
            accepted_prediction_indices.add(prediction_index)
    primary = {
        item.id for item in context.dataset.annotations if item.primary_or_alternative == "primary"
    }
    all_accepted = {
        item.id
        for item in context.dataset.annotations
        if item.primary_or_alternative in {"primary", "acceptable_alternative"}
    }

    def score(expected: set[str]) -> dict[str, Any]:
        tp = sum(min(accepted.get(item_id, 0), 1) for item_id in expected)
        counts = _counts(tp, len(predictions) - tp, len(expected) - tp)
        precision = counts["precision"]
        recall = counts["recall"]
        denominator = precision + recall if precision is not None and recall is not None else 0
        counts["f1"] = (
            2 * precision * recall / denominator
            if precision is not None and recall is not None and denominator > 0
            else 0
            if expected or predictions
            else None
        )
        counts["matched_annotation_ids"] = sorted(
            item_id for item_id in expected if accepted.get(item_id, 0)
        )
        counts["missed_annotation_ids"] = sorted(
            item_id for item_id in expected if accepted.get(item_id, 0) == 0
        )
        return counts

    return {
        "primary_only": score(primary),
        "primary_plus_acceptable_alternatives": score(all_accepted),
        "strategic_goal_predictions": len(predictions),
        "unmatched_prediction_ids": [
            index
            for index, prediction in predictions
            if prediction.candidate.semantic_type == SemanticType.STRATEGIC_GOAL
            and prediction.candidate.goal_level
            not in {GoalLevel.BUSINESS_UNIT, GoalLevel.SUBORDINATE_TARGET}
            and index not in accepted_prediction_indices
        ],
        "unresolved_goal_level_ids": [
            index
            for index, prediction in enumerate(context.predictions)
            if prediction.candidate.semantic_type == SemanticType.STRATEGIC_GOAL
            and prediction.candidate.goal_level is None
        ],
        "review_status": context.dataset.review_status,
    }


def abstraction_metric(context: GoalMetricContext) -> dict[str, Any]:
    references = {item.id: item for item in context.dataset.annotations}
    scored = []
    promotions = []
    unknown = []
    for index, prediction in enumerate(context.predictions):
        if prediction.candidate.semantic_type != SemanticType.STRATEGIC_GOAL:
            continue
        if len(prediction.annotation_matches) != 1:
            unknown.append(index)
            continue
        annotation = references[prediction.annotation_matches[0]]
        predicted_level = prediction.candidate.goal_level
        expected_level = {
            "enterprise_goal": "enterprise",
            "business_unit_goal": "business_unit",
            "subordinate_target": "subordinate_target",
        }.get(annotation.abstraction, "not_a_goal")
        correct = predicted_level == expected_level
        scored.append(correct)
        if annotation.abstraction in {"business_unit_goal", "subordinate_target"} and (
            predicted_level == "enterprise"
        ):
            promotions.append(
                {"prediction": index, "annotation": annotation.id, "level": predicted_level}
            )
    return {
        "accuracy_on_evidence_matched_predictions": (sum(scored) / len(scored) if scored else None),
        "matched_predictions": len(scored),
        "unknown_or_ambiguous_evidence_matches": unknown,
        "promotions_to_enterprise": promotions,
        "correctly_rejected_lower_level_goals": [
            prediction.annotation_matches[0]
            for prediction in context.predictions
            if prediction.candidate.semantic_type != SemanticType.STRATEGIC_GOAL
            and len(prediction.annotation_matches) == 1
            and references[prediction.annotation_matches[0]].abstraction
            in {"business_unit_goal", "subordinate_target"}
        ],
        "metric_status": "goal_level is internal extraction metadata, not an OWL class",
    }


def scope_metric(context: GoalMetricContext) -> dict[str, Any]:
    references = {item.id: item for item in context.dataset.annotations}
    scored = []
    mismatches = []
    for index, prediction in enumerate(context.predictions):
        if prediction.candidate.semantic_type != SemanticType.STRATEGIC_GOAL:
            continue
        if len(prediction.annotation_matches) != 1:
            continue
        annotation = references[prediction.annotation_matches[0]]
        actual = prediction.candidate.scope.model_dump(mode="json", exclude_none=True)
        expected = annotation.scope.model_dump(mode="json", exclude_none=True)
        correct = actual == expected
        scored.append(correct)
        if not correct:
            mismatches.append(
                {
                    "prediction": index,
                    "annotation": annotation.id,
                    "expected": expected,
                    "actual": actual,
                }
            )
    return {
        "accuracy_on_evidence_matched_predictions": sum(scored) / len(scored) if scored else None,
        "matched_predictions": len(scored),
        "mismatches": mismatches,
        "unknown_scope_is_not_group_scope": True,
    }


def duplicate_metric(context: GoalMetricContext) -> dict[str, Any]:
    references = {item.id for item in context.dataset.annotations}
    counts: dict[str, int] = defaultdict(int)
    overmerged = []
    for index, prediction in enumerate(context.predictions):
        if prediction.candidate.semantic_type != SemanticType.STRATEGIC_GOAL:
            continue
        matched = set(prediction.annotation_matches) & references
        if len(matched) > 1:
            overmerged.append({"prediction": index, "annotations": sorted(matched)})
        if len(matched) == 1:
            counts[next(iter(matched))] += 1
    return {
        "over_split_predictions": {
            annotation_id: count - 1 for annotation_id, count in sorted(counts.items()) if count > 1
        },
        "over_split_count": sum(max(0, count - 1) for count in counts.values()),
        "over_merged_predictions": overmerged,
        "metric_status": "Evidence-anchor grouping; semantic merge decisions still require review.",
    }


def provenance_metric(context: GoalMetricContext) -> dict[str, Any]:
    checked = [
        (index, prediction)
        for index, prediction in enumerate(context.predictions)
        if prediction.candidate.semantic_type == SemanticType.STRATEGIC_GOAL
    ]
    verified = [
        index
        for index, item in checked
        if item.pdf_page is not None and item.pdf_page in item.verified_pdf_pages
    ]
    invalid = [
        {"prediction": index, "reported_page": prediction.pdf_page}
        for index, prediction in checked
        if prediction.pdf_page is None or prediction.pdf_page not in prediction.verified_pdf_pages
    ]
    return {
        **_counts(len(verified), len(invalid), 0),
        "verified_predictions": verified,
        "unresolved_predictions": invalid,
        "check": "normalized whitespace substring on the reported physical PDF page",
    }


def semantic_fidelity_metric(context: GoalMetricContext) -> dict[str, Any]:
    return {
        "status": "requires_human_review",
        "matched_predictions": [
            {
                "prediction": index,
                "reference_ids": list(prediction.annotation_matches),
                "normalized_statement": prediction.candidate.statement,
                "evidence": prediction.candidate.excerpt,
            }
            for index, prediction in enumerate(context.predictions)
            if prediction.candidate.semantic_type == SemanticType.STRATEGIC_GOAL
            and prediction.annotation_matches
        ],
        "limitation": "Lexical evidence matching does not establish semantic equivalence.",
    }


def type_metric(context: GoalMetricContext) -> dict[str, Any]:
    references = {item.id: item for item in context.dataset.annotations}
    scored = []
    mismatches = []
    for index, prediction in enumerate(context.predictions):
        if len(prediction.annotation_matches) != 1:
            continue
        annotation = references[prediction.annotation_matches[0]]
        correct = prediction.candidate.semantic_type == annotation.expected_type
        scored.append(correct)
        if not correct:
            mismatches.append(
                {
                    "prediction": index,
                    "annotation": annotation.id,
                    "expected": annotation.expected_type,
                    "actual": prediction.candidate.semantic_type,
                }
            )
    return {
        "accuracy_on_evidence_matched_predictions": sum(scored) / len(scored) if scored else None,
        "matched_predictions": len(scored),
        "mismatches": mismatches,
    }


def hierarchy_metric(context: GoalMetricContext) -> dict[str, Any]:
    expected = {
        (item.from_annotation, item.to_annotation, item.property)
        for item in context.dataset.hierarchy_hypotheses
    }
    predicted = {
        (item.from_annotation, item.to_annotation, item.property)
        for item in context.hierarchy_predictions
    }
    matched = expected & predicted
    return {
        **_counts(len(matched), len(predicted - expected), len(expected - predicted)),
        "expected_hypotheses": [list(item) for item in sorted(expected)],
        "predicted_supported_links": [list(item) for item in sorted(predicted)],
        "status": "provisional",
        "limitation": (
            "Hierarchy hypotheses are synthesized annotations, not source-asserted OWL links."
        ),
    }


DEFAULT_GOAL_METRICS: tuple[GoalMetric, ...] = (
    detection_metric,
    type_metric,
    abstraction_metric,
    scope_metric,
    duplicate_metric,
    provenance_metric,
    hierarchy_metric,
    semantic_fidelity_metric,
)

METRIC_NAMES = {
    "detection_metric": "goal_detection",
    "type_metric": "ontology_type_accuracy",
    "abstraction_metric": "abstraction_accuracy",
    "scope_metric": "scope_accuracy",
    "duplicate_metric": "duplicate_quality",
    "provenance_metric": "provenance",
    "hierarchy_metric": "hierarchy_accuracy",
    "semantic_fidelity_metric": "semantic_fidelity",
}


@dataclass(frozen=True)
class _PageSegment:
    segment: Segment


@dataclass(frozen=True)
class _SegmentResult:
    segment: Segment
    result: InferenceResult
    cached: bool
    current_usage: Usage
    model_calls: int


def _printed_page(text: str) -> int | None:
    normalized = " ".join(text.split())
    match = re.search(r"Shell Annual Report and Accounts 2025\s+(\d{1,3})\b", normalized)
    if match is None:
        match = re.search(r"\b(\d{1,3})\s+Shell Annual Report and Accounts 2025\b", normalized)
    return int(match[1]) if match else None


def _audit_annotation_evidence(dataset: GoalDataset, content: bytes) -> list[dict[str, Any]]:
    failures = []
    with pymupdf.open(stream=content, filetype="pdf") as document:
        for annotation in dataset.annotations:
            for evidence in annotation.evidence:
                page_texts = (
                    document[evidence.pdf_page - 1].get_text("text", sort=False),
                    document[evidence.pdf_page - 1].get_text("text", sort=True),
                )
                if not any(
                    isinstance(page_text, str) and _norm(evidence.excerpt) in _norm(page_text)
                    for page_text in page_texts
                ):
                    failures.append(
                        {
                            "annotation_id": annotation.id,
                            "pdf_page": evidence.pdf_page,
                            "excerpt": evidence.excerpt,
                        }
                    )
    return failures


def _page_segments(
    parsed: list[Segment], revision_id, max_chars: int = 12_000
) -> tuple[dict[int, str], list[_PageSegment]]:
    grouped: dict[int, list[Segment]] = defaultdict(list)
    for segment in parsed:
        page = segment.location.get("page")
        if isinstance(page, int):
            grouped[page].append(segment)
    pages: dict[int, str] = {}
    result: list[_PageSegment] = []
    for page, blocks in sorted(grouped.items()):
        page_text = "\n".join(block.text for block in blocks)
        pages[page] = page_text
        heading = next((block.heading for block in blocks if block.heading), None)
        printed = _printed_page(page_text)
        for part, (offset, text) in enumerate(split_text(page_text, max_chars)):
            segment = Segment(
                id=stable_id(revision_id, "goal-page", page, part, text),
                revision_id=revision_id,
                ordinal=len(result),
                text=text,
                heading=heading,
                location={
                    "page": page,
                    "pdf_page": page,
                    "printed_page": printed,
                    "block_offset": offset,
                },
            )
            result.append(_PageSegment(segment=segment))
    return pages, result


async def _parse_report(
    path: Path, content: bytes, source_hash: str, cache_dir: Path, parser: StructuredParser
) -> tuple[dict[int, str], list[_PageSegment], bool]:
    cache_key = fingerprint(source=source_hash, parser=parser.version, segment_chars=12_000)
    cache_path = cache_dir / f"pages-{cache_key}.json"
    if await asyncio.to_thread(cache_path.is_file):
        payload = json.loads(await asyncio.to_thread(cache_path.read_text, encoding="utf-8"))
        parsed = [Segment.model_validate(item) for item in payload]
        revision_id = stable_id("goal-report-revision", source_hash)
        pages, segments = _page_segments(parsed, revision_id)
        return pages, segments, True

    item = SourceItem(
        id=stable_id("goal-report-source", source_hash),
        connector="evaluation",
        location=str(path),
        name=path.name,
        format="pdf",
    )
    revision = SourceRevision(
        id=stable_id("goal-report-revision", source_hash),
        source_id=item.id,
        content_hash=source_hash,
        artifact_key=f"evaluation/{source_hash}",
        source_location=str(path),
    )
    with tracer.start_as_current_span(
        "goal.evaluation.parse",
        attributes={"source.sha256": source_hash, "parser.version": parser.version},
    ):
        parsed = await parser.parse(item, revision, content)
    pages, segments = _page_segments(parsed, revision.id)
    await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)
    await _atomic_write(cache_path, json.dumps([item.model_dump(mode="json") for item in parsed]))
    return pages, segments, False


async def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    await asyncio.to_thread(temporary.write_text, text, encoding="utf-8")
    await asyncio.to_thread(os.replace, temporary, path)


def _model_calls(result: InferenceResult, inference: IngestionSkills) -> int:
    if inference.capabilities.model_id.startswith("offline-"):
        return 0
    return result.usage.model_calls


def _cache_key(segment: Segment, inference: IngestionSkills, configuration: str) -> str:
    return fingerprint(
        cache_schema="goal-evaluation-cache-v2",
        segment=segment.id,
        content=content_hash(segment.text.encode("utf-8")),
        model=inference.capabilities.model_id,
        prompt=inference.prompt_version,
        label=inference.label_fingerprint,
        extraction=inference.extraction_fingerprint,
        configuration=configuration,
    )


async def _extract_segment(
    segment: Segment,
    inference: IngestionSkills,
    cache_dir: Path,
    configuration: str,
) -> _SegmentResult:
    key = _cache_key(segment, inference, configuration)
    cache_path = cache_dir / f"result-{key}.json"
    if await asyncio.to_thread(cache_path.is_file):
        payload = json.loads(await asyncio.to_thread(cache_path.read_text, encoding="utf-8"))
        result = InferenceResult.model_validate(payload)
        return _SegmentResult(segment, result, True, Usage(), _model_calls(result, inference))
    with tracer.start_as_current_span(
        "goal.evaluation.extract",
        attributes={
            "model.id": inference.capabilities.model_id,
            "prompt.version": inference.prompt_version,
            "segment.id": str(segment.id),
            "pdf.page": segment.location.get("pdf_page", 0),
        },
    ):
        result = await inference.extract(segment)
    validate_evidence(result.extraction, segment)
    await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)
    await _atomic_write(cache_path, result.model_dump_json())
    return _SegmentResult(segment, result, False, result.usage, _model_calls(result, inference))


async def _synthesize_segment(
    segment: Segment,
    inference: IngestionSkills,
    synthesizer: GoalSynthesisPort,
    cache_dir: Path,
    configuration: str,
) -> _SegmentResult:
    key = fingerprint(
        segment=segment.id,
        content=content_hash(segment.text.encode("utf-8")),
        model=inference.capabilities.model_id,
        synthesis=synthesizer.goal_synthesis_fingerprint,
        configuration=configuration,
    )
    cache_path = cache_dir / f"goal-synthesis-{key}.json"
    if await asyncio.to_thread(cache_path.is_file):
        payload = json.loads(await asyncio.to_thread(cache_path.read_text, encoding="utf-8"))
        result = InferenceResult.model_validate(payload)
        return _SegmentResult(segment, result, True, Usage(), _model_calls(result, inference))
    result = await synthesizer.synthesize_goals(segment)
    validate_evidence(result.extraction, segment)
    await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)
    await _atomic_write(cache_path, result.model_dump_json())
    return _SegmentResult(segment, result, False, result.usage, _model_calls(result, inference))


def _make_predictions(
    runs: list[_SegmentResult], dataset: GoalDataset, page_texts: dict[int, str]
) -> tuple[tuple[GoalPrediction, ...], tuple[GoalLinkPrediction, ...]]:
    predictions = []
    links: set[tuple[str, str, Literal["supports", "refines"]]] = set()
    normalized_pages = {page: _norm(text) for page, text in page_texts.items()}
    for run in runs:
        candidate_matches: list[tuple[str, ...]] = []
        for candidate in run.result.extraction.candidates:
            excerpt = _norm(candidate.excerpt)
            verified_pages = tuple(
                page for page, text in normalized_pages.items() if excerpt and excerpt in text
            )
            location = run.segment.location
            matches = _annotation_matches(candidate, dataset)
            candidate_matches.append(matches)
            evidence_page = location.get("pdf_page")
            if (
                (not isinstance(evidence_page, int) or evidence_page < 1)
                and candidate.source_key
                and (source_page := re.fullmatch(r"pdf-page:(\d+)", candidate.source_key))
            ):
                selected_page = int(source_page.group(1))
                if selected_page in verified_pages:
                    evidence_page = selected_page
            if not isinstance(evidence_page, int) or evidence_page < 1:
                evidence_page = verified_pages[0] if len(verified_pages) == 1 else None
            printed_page = location.get("printed_page")
            if not isinstance(printed_page, int) and evidence_page is not None:
                printed_page = _printed_page(page_texts.get(evidence_page, ""))
            predictions.append(
                GoalPrediction(
                    candidate=candidate,
                    pdf_page=evidence_page,
                    printed_page=printed_page,
                    section=run.segment.heading,
                    annotation_matches=matches,
                    verified_pdf_pages=verified_pages,
                    segment_id=str(run.segment.id),
                    candidate_index=len(candidate_matches) - 1,
                )
            )
        for relation in run.result.extraction.relationships:
            if relation.kind is RelationshipKind.SUPPORTS:
                relation_property: Literal["supports", "refines"] = "supports"
            elif relation.kind is RelationshipKind.REFINES:
                relation_property = "refines"
            else:
                continue
            if relation.from_index >= len(candidate_matches) or relation.to_index >= len(
                candidate_matches
            ):
                continue
            left, right = (
                candidate_matches[relation.from_index],
                candidate_matches[relation.to_index],
            )
            if len(left) == len(right) == 1:
                links.add((left[0], right[0], relation_property))
    return (
        tuple(predictions),
        tuple(
            GoalLinkPrediction(from_annotation=left, to_annotation=right, property=prop)
            for left, right, prop in sorted(links)
        ),
    )


def _metric_results(
    dataset: GoalDataset,
    predictions: tuple[GoalPrediction, ...],
    hierarchy_predictions: tuple[GoalLinkPrediction, ...],
    metrics: tuple[GoalMetric, ...],
) -> dict[str, Any]:
    context = GoalMetricContext(
        dataset=dataset,
        predictions=predictions,
        hierarchy_predictions=hierarchy_predictions,
    )
    return {
        METRIC_NAMES.get(
            getattr(metric, "__name__", ""), getattr(metric, "__name__", type(metric).__name__)
        ): metric(context)
        for metric in metrics
    }


def _candidate_payload(predictions: tuple[GoalPrediction, ...]) -> list[dict[str, Any]]:
    return [prediction.model_dump(mode="json") for prediction in predictions]


def _enterprise_synthesis_records(
    predictions: tuple[GoalPrediction, ...],
) -> list[tuple[str, str, str, str, str]]:
    return list(
        dict.fromkeys(
            (
                prediction.candidate.statement,
                (
                    prediction.candidate.goal_level.value
                    if prediction.candidate.goal_level is not None
                    else "unclassified"
                ),
                f"pdf-page:{prediction.pdf_page}",
                prediction.section or "Unknown section",
                prediction.candidate.excerpt,
            )
            for prediction in predictions
            if prediction.candidate.semantic_type is SemanticType.STRATEGIC_GOAL
            and prediction.candidate.goal_level
            in {
                GoalLevel.ENTERPRISE,
                None,
            }
            and prediction.pdf_page is not None
            and prediction.candidate.excerpt
        )
    )


def _deduplicate_offline_candidates(runs: list[_SegmentResult]) -> list[_SegmentResult]:
    seen: set[str] = set()
    reconciled = []
    for run in runs:
        candidates = []
        index_map = {}
        for index, candidate in enumerate(run.result.extraction.candidates):
            key = fingerprint(
                semantic_type=candidate.semantic_type,
                goal_level=candidate.goal_level,
                statement=_norm(candidate.statement),
                scope=candidate.scope.model_dump(mode="json"),
            )
            if key not in seen:
                seen.add(key)
                index_map[index] = len(candidates)
                candidates.append(candidate)
        relationships = [
            relationship.model_copy(
                update={
                    "from_index": index_map[relationship.from_index],
                    "to_index": index_map[relationship.to_index],
                }
            )
            for relationship in run.result.extraction.relationships
            if relationship.from_index in index_map
            and relationship.to_index in index_map
            and index_map[relationship.from_index] != index_map[relationship.to_index]
        ]
        if candidates or relationships:
            result = run.result.model_copy(
                update={
                    "extraction": Extraction(
                        candidates=candidates,
                        relationships=relationships,
                    )
                }
            )
            reconciled.append(
                _SegmentResult(
                    segment=run.segment,
                    result=result,
                    cached=run.cached,
                    current_usage=run.current_usage,
                    model_calls=run.model_calls,
                )
            )
    return reconciled


async def _strategy(
    strategy: GoalStrategy,
    page_segments: list[_PageSegment],
    page_texts: dict[int, str],
    dataset: GoalDataset,
    inference: IngestionSkills,
    cache_dir: Path,
    configuration: str,
    max_model_calls: int,
    max_calls_per_segment: int,
    metrics: tuple[GoalMetric, ...],
) -> dict[str, Any]:
    selected_pages = set(dataset.section_aware_pdf_pages)
    selected = (
        page_segments
        if strategy == "whole_document"
        else [item for item in page_segments if item.segment.location["pdf_page"] in selected_pages]
    )
    unique: dict[str, _SegmentResult] = {}
    errors = []
    budget_consumed = 0
    unaccounted_call_reserve = 0
    started = time.monotonic()
    for item in selected:
        key = _cache_key(item.segment, inference, configuration)
        cache_path = cache_dir / f"result-{key}.json"
        cache_hit = await asyncio.to_thread(cache_path.is_file)
        reserve = (
            max_calls_per_segment
            if not cache_hit and not inference.capabilities.model_id.startswith("offline-")
            else 0
        )
        if reserve and budget_consumed + reserve > max_model_calls:
            raise ValueError(
                f"{strategy} exceeds --max-model-calls={max_model_calls} before inference"
            )
        try:
            record = await _extract_segment(item.segment, inference, cache_dir, configuration)
            unique[str(item.segment.id)] = record
            if not record.cached:
                budget_consumed += record.model_calls
        except Exception as error:
            if reserve:
                budget_consumed += reserve
                unaccounted_call_reserve += reserve
            errors.append(
                {
                    "pdf_page": item.segment.location["pdf_page"],
                    "section": item.segment.heading,
                    "error_type": type(error).__name__,
                }
            )
    runs = list(unique.values())
    final_runs = runs
    synthesis_calls = 0
    synthesis_completed = False
    reconciliation_mode = "not_applicable"
    if strategy == "hierarchical":
        if inference.capabilities.model_id.startswith("offline-"):
            final_runs = _deduplicate_offline_candidates(runs)
            synthesis_completed = True
            reconciliation_mode = "offline_exact_deduplication"
        initial, _ = _make_predictions(runs, dataset, page_texts)
        evidence_records = _enterprise_synthesis_records(initial)
        if evidence_records and not inference.capabilities.model_id.startswith("offline-"):
            synthesis_completed = False
            reconciliation_mode = "model_synthesis"
            synthesis_text = "\n\n".join(
                f"Candidate: {statement}\nLevel: {goal_level}\nSource key: {source_key}\n"
                f"Section: {section}\nEvidence: {excerpt}"
                for statement, goal_level, source_key, section, excerpt in evidence_records
            )
            if len(synthesis_text) > 16_000:
                raise ValueError("Hierarchical synthesis input exceeds the 16,000-character bound")
            synthesis_segment = Segment(
                id=stable_id("goal-synthesis", configuration, synthesis_text),
                revision_id=stable_id("goal-synthesis-revision", dataset.source.sha256),
                ordinal=len(runs),
                text=synthesis_text,
                heading="Source-grounded strategic-goal synthesis",
                location={"pdf_page": 0, "printed_page": None},
            )
            synthesis_cache = cache_dir / (
                f"result-{_cache_key(synthesis_segment, inference, configuration)}.json"
            )
            synthesis_cache_hit = await asyncio.to_thread(synthesis_cache.is_file)
            reserve = (
                max_calls_per_segment
                if not synthesis_cache_hit
                and not inference.capabilities.model_id.startswith("offline-")
                else 0
            )
            if reserve and budget_consumed + reserve > max_model_calls:
                raise ValueError(
                    "Hierarchical synthesis exceeds --max-model-calls before inference"
                )
            try:
                synthesis = (
                    await _synthesize_segment(
                        synthesis_segment,
                        inference,
                        inference,
                        cache_dir,
                        configuration,
                    )
                    if isinstance(inference, GoalSynthesisPort)
                    else await _extract_segment(
                        synthesis_segment, inference, cache_dir, configuration
                    )
                )
                synthesis_calls = synthesis.model_calls
                runs.append(synthesis)
                synthesis_completed = True
                final_runs = [synthesis]
                if not synthesis.cached:
                    budget_consumed += synthesis.model_calls
            except Exception as error:
                if reserve:
                    budget_consumed += reserve
                    unaccounted_call_reserve += reserve
                errors.append({"stage": "synthesis", "error_type": type(error).__name__})

    predictions, hierarchy_predictions = _make_predictions(final_runs, dataset, page_texts)
    output_metrics = _metric_results(dataset, predictions, hierarchy_predictions, metrics)
    usages = [record.result.usage for record in runs]
    current_usages = [record.current_usage for record in runs]
    model_calls = sum(record.model_calls for record in runs)
    usage = total_usage(usages)
    current_usage = total_usage(current_usages)
    is_offline = inference.capabilities.model_id.startswith("offline-")
    cost_known = (is_offline or not errors) and all(
        record.result.usage.cost is not None
        for record in runs
        if record.model_calls and not record.cached
    )
    current_cost = (
        sum(
            record.current_usage.cost or 0
            for record in runs
            if record.model_calls and not record.cached
        )
        if cost_known
        else None
    )
    return {
        "strategy": strategy,
        "status": "completed" if not errors else "partial",
        "segments_processed": len(selected),
        "sections_processed": len(
            {item.segment.heading for item in selected if item.segment.heading}
        ),
        "source_pages_processed": len({item.segment.location["pdf_page"] for item in selected}),
        "candidate_count": len(predictions),
        "strategic_goal_candidates": sum(
            item.candidate.semantic_type == SemanticType.STRATEGIC_GOAL for item in predictions
        ),
        "model_id": inference.capabilities.model_id,
        "prompt_version": inference.prompt_version,
        "label_fingerprint": inference.label_fingerprint,
        "extraction_fingerprint": inference.extraction_fingerprint,
        "model_calls": model_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "reported_model_latency_ms": usage.latency_ms,
        "estimated_historical_cost": usage.cost,
        "current_run_model_calls": sum(record.model_calls for record in runs if not record.cached),
        "model_call_budget_consumed": budget_consumed,
        "unaccounted_model_call_reserve": unaccounted_call_reserve,
        "model_call_count_complete": not errors,
        "current_run_input_tokens": current_usage.input_tokens,
        "current_run_output_tokens": current_usage.output_tokens,
        "current_run_cost": current_cost,
        "cache_hits": sum(record.cached for record in runs),
        "reprocessing_avoided": sum(record.cached for record in runs),
        "elapsed_ms": (time.monotonic() - started) * 1000,
        "metrics": output_metrics,
        "predictions": _candidate_payload(predictions),
        "errors": errors,
        "hierarchical_reconciliation_runs": int(synthesis_completed),
        "hierarchical_synthesis_runs": int(
            reconciliation_mode == "model_synthesis" and synthesis_completed
        ),
        "hierarchical_synthesis_model_calls": synthesis_calls,
        "hierarchical_reconciliation_mode": reconciliation_mode,
    }


async def _pdf_page_count(content: bytes) -> int:
    def count() -> int:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            return document.page_count

    return await asyncio.to_thread(count)


def _configuration_fingerprint(values: dict[str, Any] | None) -> str:
    return fingerprint(configuration=values or {})


async def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    await _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False))


def _previous_run(
    output_dir: Path, dataset_hash: str, source_hash: str, model_id: str
) -> Path | None:
    candidates = []
    for path in output_dir.glob("goal-evaluation-*.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            report.get("source", {}).get("sha256") == source_hash
            and report.get("model", {}).get("model_id") == model_id
            and report.get("dataset_sha256") == dataset_hash
        ):
            candidates.append(path)
    return max(candidates, key=lambda item: item.stat().st_mtime_ns) if candidates else None


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategic-goal evaluation",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Status: **{report['status']}**",
        f"- Source: {report['source']['name']} (`{report['source']['sha256']}`)",
        f"- Annotations: **{report['annotation_review_status']}**",
        f"- Source-verified reference excerpts: "
        f"{report['annotation_evidence_audit']['evidence_count']}",
        f"- Model: `{report['model']['model_id']}`; prompt `{report['model']['prompt_version']}`",
        f"- Model comparison: **{report['model_comparison']['status']}** "
        f"({report['model_comparison']['reason']})",
        "",
        (
            "Scores are diagnostics against provisional reference annotations; "
            "alternatives are shown separately."
        ),
        "",
    ]
    for name, result in report["strategies"].items():
        metrics = result["metrics"]
        strict = metrics["goal_detection"]["primary_only"]
        inclusive = metrics["goal_detection"]["primary_plus_acceptable_alternatives"]
        lines.extend(
            [
                f"## {name}",
                "",
                f"Processed {result['source_pages_processed']} pages / "
                f"{result['sections_processed']} sections / "
                f"{result['segments_processed']} segments; "
                f"{result['model_calls']} model calls, {result['input_tokens']} input and "
                f"{result['output_tokens']} output tokens; current-run cost "
                f"`{result['current_run_cost']}`.",
                "",
                f"Primary-only detection: precision `{strict['precision']}`, "
                f"recall `{strict['recall']}`. "
                f"With acceptable alternatives: precision `{inclusive['precision']}`, "
                f"recall `{inclusive['recall']}`.",
                f"Missed primary references: "
                f"{', '.join(strict['missed_annotation_ids']) or 'none'}.",
                f"Missed references including alternatives: "
                f"{', '.join(inclusive['missed_annotation_ids']) or 'none'}.",
                "",
                "| Candidate | Type | Goal level | Evidence page | Reference match | "
                "Source verified |",
                "|---|---|---|---:|---|---|",
            ]
        )
        for item in result["predictions"]:
            candidate = item["candidate"]
            statement = candidate["statement"].replace("|", "&#124;")
            lines.append(
                f"| {statement} | {candidate.get('semantic_type')} | "
                f"{candidate.get('goal_level')} | "
                f"{item.get('printed_page') or item.get('pdf_page')} | "
                f"{', '.join(item['annotation_matches']) or 'unmatched'} | "
                f"{item.get('pdf_page') in item['verified_pdf_pages']} |"
            )
        detection = metrics["goal_detection"]
        lines.extend(
            [
                "",
                f"Over-split: {metrics['duplicate_quality']['over_split_count']}; "
                f"over-merged: {len(metrics['duplicate_quality']['over_merged_predictions'])}.",
                f"Scope accuracy on matched candidates: "
                f"{metrics['scope_accuracy']['accuracy_on_evidence_matched_predictions']}.",
                f"Abstraction accuracy on matched candidates: "
                f"{metrics['abstraction_accuracy']['accuracy_on_evidence_matched_predictions']}; "
                f"enterprise promotions: "
                f"{len(metrics['abstraction_accuracy']['promotions_to_enterprise'])}.",
                f"Provenance unresolved: {len(metrics['provenance']['unresolved_predictions'])}.",
                f"Hierarchy assessment: {metrics['hierarchy_accuracy']['status']}.",
                f"Semantic fidelity: {metrics['semantic_fidelity']['status']}.",
                "",
            ]
        )
        if detection["unmatched_prediction_ids"]:
            lines.append("False-positive or unclassified enterprise-goal candidates:")
            for index in detection["unmatched_prediction_ids"]:
                candidate = result["predictions"][index]["candidate"]
                lines.append(
                    f"- `{candidate['statement']}` — not accepted as a primary enterprise goal."
                )
            lines.append("")
    lines.extend(
        [
            "## Limitations",
            "",
            (
                "- Semantic fidelity and hierarchy are not inferred from lexical overlap; "
                "qualitative adjudication remains open."
            ),
            "- No human-independent gold review is claimed.",
            "- Cached strategy results report historical usage separately from current-run spend.",
            "",
        ]
    )
    return "\n".join(lines)


async def evaluate_goals(
    source_path: Path,
    dataset_path: Path,
    output_dir: Path,
    parser: StructuredParser,
    inference: IngestionSkills,
    *,
    strategies: tuple[GoalStrategy, ...] = ("section_aware",),
    configuration: dict[str, Any] | None = None,
    max_model_calls: int = 2000,
    max_calls_per_segment: int = 18,
    metrics: tuple[GoalMetric, ...] = DEFAULT_GOAL_METRICS,
) -> dict[str, Any]:
    dataset_raw = await asyncio.to_thread(dataset_path.read_bytes)
    dataset = GoalDataset.model_validate_json(dataset_raw)
    content = await asyncio.to_thread(source_path.read_bytes)
    source_hash = content_hash(content)
    if source_hash != dataset.source.sha256:
        raise ValueError("Source PDF SHA-256 does not match the reviewed reference dataset")
    page_count = await _pdf_page_count(content)
    if page_count != dataset.source.pdf_pages:
        raise ValueError("Source PDF page count does not match the reference dataset")
    annotation_evidence_failures = await asyncio.to_thread(
        _audit_annotation_evidence, dataset, content
    )
    if annotation_evidence_failures:
        references = ", ".join(
            f"{item['annotation_id']} p.{item['pdf_page']}" for item in annotation_evidence_failures
        )
        raise ValueError(f"Dataset evidence does not match cited source pages: {references}")
    if not strategies or len(set(strategies)) != len(strategies):
        raise ValueError("Select one or more distinct extraction strategies")
    if max_model_calls < 1:
        raise ValueError("max_model_calls must be positive")
    if max_calls_per_segment < 1:
        raise ValueError("max_calls_per_segment must be positive")
    configuration_id = _configuration_fingerprint(configuration)
    cache_dir = output_dir / "cache"
    page_texts, segments, parse_cache_hit = await _parse_report(
        source_path, content, source_hash, cache_dir, parser
    )
    if not segments:
        raise ValueError("The PDF parser produced no text segments")

    with tracer.start_as_current_span(
        "goal.evaluation",
        attributes={
            "source.sha256": source_hash,
            "dataset.version": dataset.version,
            "model.id": inference.capabilities.model_id,
            "strategy.count": len(strategies),
        },
    ) as span:
        results = {}
        for strategy in strategies:
            remaining_calls = max_model_calls - sum(
                result["current_run_model_calls"] for result in results.values()
            )
            results[strategy] = await _strategy(
                strategy,
                segments,
                page_texts,
                dataset,
                inference,
                cache_dir,
                configuration_id,
                remaining_calls,
                max_calls_per_segment,
                metrics,
            )
        span.set_attribute(
            "model.calls",
            sum(result["model_call_budget_consumed"] for result in results.values()),
        )

    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    report: dict[str, Any] = {
        "schema_version": "goal-evaluation-v1",
        "run_id": run_id,
        "recorded_at": now().isoformat(),
        "status": (
            "completed"
            if all(result["status"] == "completed" for result in results.values())
            else "partial"
        ),
        "source": dataset.source.model_dump(mode="json"),
        "dataset_version": dataset.version,
        "dataset_sha256": content_hash(dataset_raw),
        "annotation_review_status": dataset.review_status,
        "annotation_note": dataset.annotation_note,
        "annotation_evidence_audit": {
            "status": "passed",
            "evidence_count": sum(len(item.evidence) for item in dataset.annotations),
            "check": "normalized whitespace substring in either PDF text reading order",
        },
        "model": {
            "model_id": inference.capabilities.model_id,
            "prompt_version": inference.prompt_version,
            "label_fingerprint": inference.label_fingerprint,
            "extraction_fingerprint": inference.extraction_fingerprint,
            "configuration_fingerprint": configuration_id,
            **(configuration or {}),
        },
        "model_comparison": {
            "status": (
                "skipped"
                if inference.capabilities.model_id.startswith("offline-")
                else "single_profile"
            ),
            "reason": (
                "The selected profile uses offline rules."
                if inference.capabilities.model_id.startswith("offline-")
                else "This run used one profile; compare separately configured profiles."
            ),
        },
        "parser": {"version": parser.version, "cache_hit": parse_cache_hit},
        "document": {
            "pdf_pages": page_count,
            "parsed_pages": len(page_texts),
            "parsed_segments": len(segments),
        },
        "strategies": results,
    }
    previous = _previous_run(
        output_dir, report["dataset_sha256"], source_hash, inference.capabilities.model_id
    )
    if previous:
        prior = json.loads(await asyncio.to_thread(previous.read_text, encoding="utf-8"))
        report["previous_run"] = previous.name
        deltas = {}
        for strategy, current in results.items():
            old = prior.get("strategies", {}).get(strategy)
            if old:
                for key in ("goal_detection", "abstraction_accuracy", "scope_accuracy"):
                    metric_name = (
                        "goal_detection"
                        if key == "goal_detection"
                        else "abstraction_accuracy"
                        if key == "abstraction_accuracy"
                        else "scope_accuracy"
                    )
                    current_value = current["metrics"][metric_name]
                    old_value = old["metrics"][metric_name]
                    if key == "goal_detection":
                        current_value = current_value["primary_only"]["f1"]
                        old_value = old_value["primary_only"]["f1"]
                    else:
                        current_value = current_value.get(
                            "accuracy_on_evidence_matched_predictions"
                        )
                        old_value = old_value.get("accuracy_on_evidence_matched_predictions")
                    if current_value is not None and old_value is not None:
                        deltas.setdefault(strategy, {})[key] = current_value - old_value
        report["previous_metric_deltas"] = deltas
    json_path = output_dir / f"goal-evaluation-{run_id}.json"
    markdown_path = output_dir / f"goal-evaluation-{run_id}.md"
    await _atomic_json(json_path, report)
    await _atomic_write(markdown_path, _markdown(report))
    report["json_report"] = str(json_path)
    report["markdown_report"] = str(markdown_path)
    return report


async def report_latest(output_dir: Path) -> tuple[dict[str, Any], Path]:
    paths = await asyncio.to_thread(lambda: list(output_dir.glob("goal-evaluation-*.json")))
    if not paths:
        raise FileNotFoundError(f"No strategic-goal evaluations exist under {output_dir}")
    path = max(paths, key=lambda item: item.stat().st_mtime_ns)
    report = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    return report, path.with_suffix(".md")


def completed_strategies(value: str) -> tuple[GoalStrategy, ...]:
    choices: dict[str, GoalStrategy] = {
        "whole-document": "whole_document",
        "section-aware": "section_aware",
        "hierarchical": "hierarchical",
    }
    if value not in choices:
        raise ValueError(f"Unknown strategy {value!r}; choose from {', '.join(choices)}")
    return (choices[value],)
