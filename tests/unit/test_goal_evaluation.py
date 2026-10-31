from __future__ import annotations

from pathlib import Path

import pymupdf

from ekb.adapters.inference import RulesInference
from ekb.adapters.parsers import StructuredParser
from ekb.domain.models import (
    Candidate,
    Capabilities,
    ContentLabeling,
    Extraction,
    GoalLevel,
    InferenceResult,
    LabelingResult,
    Scope,
    Segment,
    SemanticType,
    Usage,
    content_hash,
    stable_id,
)
from ekb.evaluation.goals import (
    GoalAnnotation,
    GoalDataset,
    GoalEvidence,
    GoalMetricContext,
    GoalPrediction,
    GoalSource,
    _annotation_matches,
    _goal_synthesis_chunks,
    _goal_synthesis_text,
    abstraction_metric,
    detection_metric,
    duplicate_metric,
    evaluate_goals,
    provenance_metric,
    report_latest,
)

_ENTERPRISE_EVIDENCE = "We aim to become a resilient enterprise."
_TARGET_EVIDENCE = "Grow regional sales by 5% annually."
_HISTORY_EVIDENCE = "Sales increased by 11% in 2025."


def test_hierarchical_reconciliation_chunks_inputs_without_splitting_records():
    records = [
        (
            f"Goal {index}",
            "StrategicGoal",
            "enterprise",
            "{}",
            f"pdf-page:{index}",
            "Strategy",
            f"Evidence {index}",
        )
        for index in range(1, 4)
    ]

    chunks = _goal_synthesis_chunks(records, max_chars=130)

    assert [record for chunk in chunks for record in chunk] == records
    assert all(len(_goal_synthesis_text(chunk)) <= 130 for chunk in chunks)


def _dataset(source: GoalSource) -> GoalDataset:
    return GoalDataset(
        version="test-v1",
        review_status="provisional_reference_annotations",
        annotation_note="Synthetic labels for unit tests.",
        source=source,
        section_aware_pdf_pages=(1,),
        annotations=(
            GoalAnnotation(
                id="SG-01",
                expected_type=SemanticType.STRATEGIC_GOAL,
                abstraction="enterprise_goal",
                primary_or_alternative="primary",
                normalized_statement="Become a resilient enterprise.",
                scope=Scope(organization="Acme"),
                evidence=(
                    GoalEvidence(
                        excerpt=_ENTERPRISE_EVIDENCE,
                        printed_page=1,
                        pdf_page=1,
                        section="Strategy",
                        strength="direct",
                        explicitness="explicit",
                    ),
                ),
                rationale="Synthetic positive example.",
                ambiguity="None.",
            ),
            GoalAnnotation(
                id="NEG-01",
                expected_type=SemanticType.STRATEGIC_GOAL,
                abstraction="subordinate_target",
                primary_or_alternative="negative_example",
                normalized_statement="Grow regional sales by 5%.",
                scope=Scope(organization="Acme", business_unit="Region"),
                evidence=(
                    GoalEvidence(
                        excerpt=_TARGET_EVIDENCE,
                        printed_page=1,
                        pdf_page=1,
                        section="Strategy",
                        strength="direct",
                        explicitness="explicit",
                    ),
                ),
                rationale="Synthetic lower-level target.",
                ambiguity="It should not be promoted to enterprise level.",
            ),
            GoalAnnotation(
                id="NEG-02",
                expected_type=None,
                abstraction="historical_fact",
                primary_or_alternative="negative_example",
                normalized_statement="Sales increased by 11% in 2025.",
                scope=Scope(organization="Acme"),
                evidence=(
                    GoalEvidence(
                        excerpt=_HISTORY_EVIDENCE,
                        printed_page=1,
                        pdf_page=1,
                        section="Performance",
                        strength="direct",
                        explicitness="explicit",
                    ),
                ),
                rationale="Synthetic historical result.",
                ambiguity="None.",
            ),
        ),
    )


def _prediction(
    excerpt: str,
    level: GoalLevel | None,
    *,
    scope: Scope | None = None,
    pdf_page: int | None = 1,
) -> GoalPrediction:
    candidate = Candidate(
        statement=excerpt,
        excerpt=excerpt,
        semantic_type=SemanticType.STRATEGIC_GOAL,
        goal_level=level,
        scope=scope or Scope(organization="Acme"),
    )
    return GoalPrediction(
        candidate=candidate,
        pdf_page=pdf_page,
        printed_page=1,
        section="Strategy",
        annotation_matches=(),
        verified_pdf_pages=(1,) if pdf_page == 1 else (),
        segment_id="test-segment",
        candidate_index=0,
    )


def test_detection_abstraction_and_duplicate_metrics_reject_promoted_targets():
    dataset = _dataset(
        GoalSource(
            id="test",
            name="Synthetic",
            filename="synthetic.pdf",
            url="https://example.invalid/synthetic.pdf",
            sha256="0" * 64,
            pdf_pages=1,
            printed_page_offset_note="No offset.",
            attribution="Synthetic test data.",
        )
    )
    predictions = (
        _prediction(_ENTERPRISE_EVIDENCE, GoalLevel.ENTERPRISE),
        _prediction(
            _TARGET_EVIDENCE,
            GoalLevel.SUBORDINATE_TARGET,
            scope=Scope(organization="Acme", business_unit="Region"),
        ),
        _prediction(_HISTORY_EVIDENCE, None),
        _prediction(_ENTERPRISE_EVIDENCE, GoalLevel.ENTERPRISE),
        _prediction(
            _TARGET_EVIDENCE,
            GoalLevel.ENTERPRISE,
            scope=Scope(organization="Acme", business_unit="Region"),
        ),
    )
    predictions = tuple(
        prediction.model_copy(
            update={
                "annotation_matches": (
                    (
                        "SG-01"
                        if prediction.candidate.excerpt == _ENTERPRISE_EVIDENCE
                        else "NEG-01"
                        if prediction.candidate.excerpt == _TARGET_EVIDENCE
                        else "NEG-02"
                    ),
                )
            }
        )
        for prediction in predictions
    )
    context = GoalMetricContext(dataset=dataset, predictions=predictions)

    detection = detection_metric(context)
    assert detection["primary_only"]["tp"] == 1
    assert detection["primary_only"]["fp"] == 3
    assert detection["primary_only"]["f1"] == 0.4
    assert abstraction_metric(context)["promotions_to_enterprise"] == [
        {"prediction": 4, "annotation": "NEG-01", "level": "enterprise"}
    ]
    assert abstraction_metric(context)["accuracy_on_evidence_matched_predictions"] == 0.6
    assert duplicate_metric(context)["over_split_count"] == 2


def test_annotation_matching_accepts_a_minimal_exact_subspan_of_reference_evidence():
    source = GoalSource(
        id="test",
        name="Synthetic",
        filename="synthetic.pdf",
        url="https://example.invalid/synthetic.pdf",
        sha256="0" * 64,
        pdf_pages=1,
        printed_page_offset_note="No offset.",
        attribution="Synthetic test data.",
    )
    dataset = _dataset(source)
    candidate = Candidate(
        statement="Become resilient.",
        excerpt="become a resilient enterprise",
        semantic_type=SemanticType.STRATEGIC_GOAL,
        goal_level=GoalLevel.ENTERPRISE,
    )

    assert _annotation_matches(candidate, dataset) == ("SG-01",)


class _FakeInference:
    capabilities = Capabilities(model_id="test-goal-model")
    prompt_version = "test-prompt-v1"
    label_fingerprint = "test-label"
    extraction_fingerprint = "test-extraction"

    def __init__(self) -> None:
        self.calls = 0

    async def label(self, segment: Segment) -> LabelingResult:
        raise NotImplementedError

    async def extract_scoped(self, segment: Segment, labeling: ContentLabeling) -> InferenceResult:
        raise NotImplementedError

    async def extract(self, segment: Segment) -> InferenceResult:
        self.calls += 1
        candidates = []
        if _ENTERPRISE_EVIDENCE in segment.text:
            candidates.append(
                Candidate(
                    statement="Become a resilient enterprise.",
                    excerpt=_ENTERPRISE_EVIDENCE,
                    semantic_type=SemanticType.STRATEGIC_GOAL,
                    goal_level=GoalLevel.ENTERPRISE,
                    scope=Scope(organization="Acme"),
                )
            )
        if _TARGET_EVIDENCE in segment.text:
            candidates.append(
                Candidate(
                    statement="Grow regional sales by 5% annually.",
                    excerpt=_TARGET_EVIDENCE,
                    semantic_type=SemanticType.STRATEGIC_GOAL,
                    goal_level=GoalLevel.SUBORDINATE_TARGET,
                    scope=Scope(organization="Acme"),
                )
            )
        return InferenceResult(
            extraction=Extraction(candidates=candidates),
            usage=Usage(input_tokens=10, output_tokens=5, model_calls=1, cost=0.01),
            model_id=self.capabilities.model_id,
            prompt_version=self.prompt_version,
        )


async def test_hierarchical_results_are_reconciled_and_cached_usage_is_separate(tmp_path: Path):
    document = pymupdf.open()
    document.new_page().insert_text(
        (72, 72),
        f"Strategy\n{_ENTERPRISE_EVIDENCE}\n{_TARGET_EVIDENCE}\n{_HISTORY_EVIDENCE}",
    )
    content = document.tobytes()
    document.close()
    source_path = tmp_path / "synthetic.pdf"
    source_path.write_bytes(content)
    source = GoalSource(
        id="synthetic",
        name="Synthetic report",
        filename=source_path.name,
        url="https://example.invalid/synthetic.pdf",
        sha256=content_hash(content),
        pdf_pages=1,
        printed_page_offset_note="No offset.",
        attribution="Synthetic test data.",
    )
    dataset = _dataset(source)
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(dataset.model_dump_json(), encoding="utf-8")
    inference = _FakeInference()
    output = tmp_path / "evaluations"

    hierarchical = await evaluate_goals(
        source_path,
        dataset_path,
        output,
        StructuredParser(),
        inference,
        strategies=("hierarchical",),
    )
    result = hierarchical["strategies"]["hierarchical"]
    assert result["candidate_count"] == 2
    assert {prediction["candidate"]["goal_level"] for prediction in result["predictions"]} == {
        "enterprise",
        "subordinate_target",
    }
    assert result["metrics"]["goal_detection"]["primary_only"]["tp"] == 1
    assert result["current_run_model_calls"] == 2
    assert result["current_run_cost"] == 0.02

    cached = await evaluate_goals(
        source_path,
        dataset_path,
        output,
        StructuredParser(),
        inference,
        strategies=("section_aware",),
    )
    cached_result = cached["strategies"]["section_aware"]
    assert cached_result["cache_hits"] == 1
    assert cached_result["current_run_model_calls"] == 0
    assert cached_result["model_calls"] == 1
    assert cached_result["current_run_cost"] == 0
    latest, markdown_path = await report_latest(output)
    assert latest["run_id"] == cached["run_id"]
    assert markdown_path.is_file()


async def test_offline_rules_capture_explicit_enterprise_vision_across_pdf_line_breaks():
    segment = Segment(
        id=stable_id("vision-segment"),
        revision_id=stable_id("vision-revision"),
        ordinal=0,
        text="Our vision\n is to become the world's leading\nintegrated energy company.",
    )
    result = await RulesInference().extract(segment)
    candidate = next(
        candidate
        for candidate in result.extraction.candidates
        if candidate.semantic_type == SemanticType.STRATEGIC_GOAL
        and candidate.goal_level == GoalLevel.ENTERPRISE
    )
    assert candidate.excerpt in segment.text
    assert candidate.statement == (
        "Our vision is to become the world's leading integrated energy company."
    )


async def test_offline_rules_do_not_invent_a_goal_from_security_context():
    segment = Segment(
        id=stable_id("security-context"),
        revision_id=stable_id("security-context-revision"),
        ordinal=0,
        text="Shell uses Azure. NIST CSF is a framework for cybersecurity.",
    )
    result = await RulesInference().extract(segment)
    assert not result.extraction.candidates


async def test_offline_rules_do_not_promote_generic_aim_language_to_enterprise_goals():
    segment = Segment(
        id=stable_id("generic-aim"),
        revision_id=stable_id("generic-aim-revision"),
        ordinal=0,
        text="We aim to reduce costs by 5% next year.",
    )
    result = await RulesInference().extract(segment)
    assert not result.extraction.candidates


def test_provenance_requires_the_reported_page_to_match_the_excerpt():
    dataset = _dataset(
        GoalSource(
            id="test",
            name="Synthetic",
            filename="synthetic.pdf",
            url="https://example.invalid/synthetic.pdf",
            sha256="0" * 64,
            pdf_pages=1,
            printed_page_offset_note="No offset.",
            attribution="Synthetic test data.",
        )
    )
    prediction = _prediction(_ENTERPRISE_EVIDENCE, GoalLevel.ENTERPRISE, pdf_page=2)
    prediction = prediction.model_copy(update={"verified_pdf_pages": (1,)})
    result = provenance_metric(GoalMetricContext(dataset=dataset, predictions=(prediction,)))
    assert result["tp"] == 0
    assert result["fp"] == 1
    assert result["unresolved_predictions"] == [{"prediction": 0, "reported_page": 2}]
