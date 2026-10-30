import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest

from ekb.adapters.inference import RulesInference
from ekb.adapters.parsers import StructuredParser
from ekb.domain.models import SemanticType, SourceItem, SourceRevision
from ekb.evaluation.nist import (
    REFERENCE,
    CaseEvidence,
    NativeIdentity,
    NistPredictions,
    SemanticPrediction,
    evaluate_nist,
    load_nist_reference,
    score_nist,
)


def _core_pdf(identifier="NIST CSWP 29"):
    with pymupdf.open() as pdf:
        for text in (
            f"{identifier}\nThe NIST Cybersecurity Framework (CSF) 2.0\n"
            "Appendix A. CSF Core ........ 15\n1",
            "NIST CSWP 29\nFebruary 26, 2024\n15\n"
            "Appendix A. CSF Core\n"
            "DIRECTION (ZX): Risk is managed.\n"
            "\u00b7 Identity (ZX.AA): Identity matters.\n"
            "o ZX.AA-01: Services are",
            "NIST CSWP 29\nFebruary 26, 2024\n16\n"
            "authenticated.\n"
            "o ZX.AA-03: The objective is to protect data.",
            "Appendix B. CSF Tiers\no ZX.AA-99: This is not a Core outcome.\n17",
        ):
            pdf.new_page().insert_text((30, 30), text, fontsize=10)
        return pdf.tobytes()


@pytest.mark.parametrize("metadata", [{}, {"framework_namespace": "nist-csf"}])
@pytest.mark.parametrize("identifier", ["NIST CSWP 29", "https://doi.org/10.6028/NIST.CSWP.29"])
async def test_native_csf_preserves_hierarchy_cross_page_evidence_and_unknown_class(
    metadata, identifier
):
    item = SourceItem(
        id=uuid4(),
        connector="test",
        location="urn:test:csf",
        name="Synthetic Core",
        format="pdf",
        metadata=metadata,
    )
    revision = SourceRevision(
        id=uuid4(), source_id=item.id, content_hash="test", artifact_key="test"
    )
    parser = StructuredParser()
    parsed = await parser.parse(item, revision, _core_pdf(identifier))
    segments = [segment for segment in parsed if "csf_node" in segment.location]
    if not metadata:
        surrounding = [segment for segment in parsed if "csf_node" not in segment.location]
        assert any("CSF Core ........" in segment.text for segment in surrounding)
        assert any("Appendix B." in segment.text for segment in surrounding)
        native_ids = {segment.id for segment in segments}
        assert all(segment.parent_id not in native_ids for segment in surrounding)
    assert [segment.location["control_id"] for segment in segments] == [
        "ZX",
        "ZX.AA",
        "ZX.AA-01",
        "ZX.AA-03",
    ]
    assert segments[2].parent_id == segments[3].parent_id == segments[1].id
    assert segments[1].parent_id == segments[0].id
    assert segments[2].text == "ZX.AA-01: Services are authenticated."
    assert [part["pdf_page"] for part in segments[2].location["csf_node"]["passages"]] == [2, 3]
    rules = RulesInference()
    labeling = await rules.label(segments[3])
    result = await rules.extract_scoped(segments[3], labeling.labeling)
    assert len(result.extraction.candidates) == 1
    candidate = result.extraction.candidates[0]
    assert candidate.source_key == "ZX.AA-03"
    assert candidate.semantic_type is None
    assert candidate.excerpt == segments[3].text
    with pytest.raises(ValueError, match="complete Core"):
        await parser.parse(
            item.model_copy(update={"metadata": {"framework_namespace": "nist-csf", "pages": [2]}}),
            revision,
            _core_pdf(),
        )


def test_supplied_sample_is_incomplete_and_missing_negative_decisions_are_not_passes():
    reference = load_nist_reference()
    predictions = NistPredictions.model_validate_json(
        (REFERENCE / "predictions_sample.json").read_bytes()
    )
    report = score_nist(reference, predictions)
    assert report["identity_metrics"]["functions"]["tp"] == 2
    assert report["identity_metrics"]["subcategories"]["fn"] == 104
    assert report["hierarchy"]["correct_links"] == 3
    assert len(report["hierarchy"]["missing_or_wrong_links"]) == 125
    assert report["curated_semantics"]["correct_classes"] == 1
    assert report["curated_semantics"]["verified_provenance"] == 0
    assert report["negative_cases"][0]["result"] == "not_run"
    assert report["negative_cases"][2]["result"] == "pass"
    assert reference.location_corrections
    assert any(item.id == "RC.RP-03" for item in reference.location_corrections)


def test_scoring_separates_candidate_type_adoption_provenance_and_duplicate_identity():
    reference = load_nist_reference()
    node = next(node for node in reference.source.nodes if node.id == "PR.DS-02")
    passage = node.passages[0]
    candidate = SemanticPrediction(
        id=node.id,
        ontology_class=SemanticType.STRATEGIC_REQUIREMENT,
        authority="external_nonprescriptive_guidance",
        normalized_statement=passage.excerpt,
        evidence=CaseEvidence(
            printed_page=passage.printed_page,
            pdf_page=passage.pdf_page,
            excerpt=passage.excerpt,
        ),
    )
    predictions = NistPredictions(
        functions=(NativeIdentity(id="PR"), NativeIdentity(id="PR")),
        semantic_cases=(candidate,),
    )
    report = score_nist(reference, predictions)
    assert report["identity_metrics"]["functions"]["fp"] == 1
    assert report["authority_and_scope"]["invented_adoption"] == []
    assert report["authority_and_scope"]["strategic_requirement_candidates"] == ["PR.DS-02"]
    assert report["curated_semantics"]["verified_provenance"] == 1
    wrong = candidate.model_copy(
        update={
            "enterprise_adopted": True,
            "normalized_statement": "All data must use TLS 1.3.",
            "evidence": candidate.evidence.model_copy(update={"pdf_page": passage.pdf_page + 1}),
        }
    )
    report = score_nist(reference, predictions.model_copy(update={"semantic_cases": (wrong,)}))
    assert report["authority_and_scope"]["invented_adoption"] == ["PR.DS-02"]
    assert report["authority_and_scope"]["invented_numeric_qualifiers"] == ["PR.DS-02"]
    assert report["curated_semantics"]["verified_provenance"] == 0
    wrong = wrong.model_copy(
        update={"evidence": wrong.evidence.model_copy(update={"passages": (passage,)})}
    )
    report = score_nist(reference, predictions.model_copy(update={"semantic_cases": (wrong,)}))
    assert report["curated_semantics"]["verified_provenance"] == 0


async def test_nist_offline_extraction_is_grounded_replayable_and_not_qualified(tmp_path):
    parser = StructuredParser()
    first = await evaluate_nist(None, tmp_path, parser, RulesInference())
    assert first["passed"] and not first["qualified"]
    assert first["metrics"]["curated_semantics"]["identified"] == 25
    assert first["metrics"]["curated_semantics"]["verified_provenance"] == 25
    assert first["metrics"]["curated_semantics"]["correct_classes"] == 0
    assert all(item["result"] == "not_run" for item in first["metrics"]["negative_cases"])

    class NoCalls(RulesInference):
        async def label(self, segment):
            raise AssertionError("Unchanged source extraction must be replayed")

    second = await evaluate_nist(None, tmp_path, parser, NoCalls())
    assert not second["errors"]
    assert second["cache_hits"] == 25
    assert second["current_usage"]["model_calls"] == 0
    assert second["previous_run"]
    assert second["correct_class_delta"] == 0
    persisted = json.loads(await asyncio.to_thread(Path(second["json_report"]).read_bytes))
    assert persisted["metrics"] == second["metrics"]
    exported = tmp_path / "predictions.json"
    payload = {
        **first["predictions"],
        "negative_case_decisions": [{"id": "NEG-01", "decision": "accept"}],
    }
    await asyncio.to_thread(exported.write_text, json.dumps(payload), encoding="utf-8")
    rejected = await evaluate_nist(None, tmp_path, parser, NoCalls(), imported_predictions=exported)
    assert not rejected["passed"]
    assert rejected["metrics"]["negative_cases"][0]["result"] == "fail"
    source = tmp_path / "changed.pdf"
    await asyncio.to_thread(source.write_bytes, b"Changed original")
    with pytest.raises(ValueError, match="hash"):
        await evaluate_nist(source, tmp_path, parser, NoCalls())
