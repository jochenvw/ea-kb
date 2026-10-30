import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pymupdf
import pytest
from pydantic import ValidationError

from ekb.adapters.inference import AgentFrameworkInference, OpenAIInference, RulesInference
from ekb.adapters.parsers import StructuredParser
from ekb.adapters.sources import FilesystemConnector
from ekb.config import Settings
from ekb.domain.grounding import GroundingError
from ekb.domain.models import (
    Candidate,
    Capabilities,
    ContentCoverage,
    ContentLabel,
    ContentLabeling,
    Extraction,
    InferenceResult,
    LabelingResult,
    ProposedRelationship,
    RelationshipKind,
    Scope,
    Segment,
    SemanticType,
    SourceItem,
    SourceRevision,
    Usage,
    content_hash,
)
from ekb.domain.skills import validate_labeling, validate_scoped_extraction
from ekb.evaluation.dataset import load_dataset
from ekb.evaluation.skills import (
    SkillDataset,
    _audit_document_sources,
    document_metrics,
    evaluate_skills,
    label_metrics,
)
from ekb.pipeline import IngestionPipeline

DATASET = Path("evals") / "skills-v1.json"
GATES = Path("evals") / "gates.json"
DOCUMENTS = Path("evals") / "documents-v1.json"


def scoped_input():
    text = "For Unit A, our goal is to improve service quality."
    segment = Segment(id=uuid4(), revision_id=uuid4(), ordinal=0, text=text)
    labels = ContentLabeling(
        labels=[
            ContentLabel(
                semantic_type=SemanticType.STRATEGIC_GOAL,
                excerpt=text,
                scope=Scope(business_unit="Unit A"),
            )
        ]
    )
    extraction = Extraction(
        candidates=[
            Candidate(
                semantic_type=SemanticType.STRATEGIC_GOAL,
                statement=text,
                excerpt=text,
                scope=Scope(business_unit="Unit A"),
            )
        ]
    )
    return segment, labels, extraction


async def test_two_skills_route_mixed_scope_and_account_for_separate_models():
    segment, labels, extraction = scoped_input()
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        is_labeling = body["model"] == "tiny-labeler"
        request_data = json.loads(body["messages"][1]["content"])
        if is_labeling:
            assert "labeling" not in request_data
            text = labels.model_dump_json()
        else:
            assert request_data["labeling"] == labels.model_dump(mode="json")
            text = extraction.model_dump_json()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = OpenAIInference(
            Settings(
                model_id="precise-extractor",
                labeling_model_id="tiny-labeler",
                labeling_input_price=1,
                labeling_output_price=2,
                model_input_price=2,
                model_output_price=4,
            ),
            client=client,
        )
        result = await adapter.extract(segment)
    assert [call["model"] for call in calls] == ["tiny-labeler", "precise-extractor"]
    assert result.extraction == extraction
    assert result.labeling and result.labeling.model_id == "tiny-labeler"
    assert result.usage.input_tokens == 20
    assert result.usage.cost == pytest.approx(0.00006)


async def test_extraction_retries_scope_widening_without_relabeling():
    segment, labels, extraction = scoped_input()
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        value = (
            extraction
            if len(calls) > 1
            else Extraction(
                candidates=[extraction.candidates[0].model_copy(update={"scope": Scope()})]
            )
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": value.model_dump_json()}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await OpenAIInference(Settings(model_id="test"), client=client).extract_scoped(
            segment, labels
        )
    assert result.extraction == extraction
    assert len(calls) == 2 and result.usage.validation_failures == 1


async def test_domain_and_irrelevant_labels_do_not_trigger_extraction():
    segment, _, _ = scoped_input()

    def forbidden(request):
        raise AssertionError("Domain-only/irrelevant content needs no extraction call")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        adapter = OpenAIInference(Settings(model_id="test"), client=client)
        for labels in (
            ContentLabeling(),
            ContentLabeling(
                labels=[
                    ContentLabel(
                        semantic_type=SemanticType.ARCHITECTURE_DOMAIN, excerpt=segment.text
                    )
                ]
            ),
            ContentLabeling(
                labels=[
                    ContentLabel(
                        semantic_type=SemanticType.ORGANIZATIONAL_SCOPE, excerpt=segment.text
                    )
                ]
            ),
        ):
            result = await adapter.extract_scoped(segment, labels)
            assert result.extraction == Extraction()
            assert result.usage.cost == 0


def test_fabricated_scope_and_kind_layer_disagreement_are_rejected():
    segment, labels, extraction = scoped_input()
    forged = labels.model_copy(
        update={
            "labels": [
                labels.labels[0].model_copy(
                    update={"scope": Scope(organization="Entire Enterprise")}
                )
            ]
        }
    )
    with pytest.raises(GroundingError, match="verbatim"):
        validate_labeling(forged, segment)
    wrong_layer = extraction.model_copy(
        update={
            "candidates": [
                extraction.candidates[0].model_copy(
                    update={"semantic_type": SemanticType.STRATEGIC_REQUIREMENT}
                )
            ]
        }
    )
    with pytest.raises(GroundingError, match="match"):
        validate_scoped_extraction(wrong_layer, segment, labels)
    with pytest.raises(ValidationError, match="Extra inputs"):
        Candidate.model_validate(
            {
                "kind": "objective",
                "semantic_type": "TechnicalRequirement",
                "statement": segment.text,
                "excerpt": segment.text,
            }
        )


def test_relationship_cannot_escape_the_labeled_scope():
    segment, labels, extraction = scoped_input()
    extraction = Extraction(
        candidates=[
            extraction.candidates[0],
            extraction.candidates[0].model_copy(update={"statement": "Improve service quality."}),
        ],
        relationships=[
            ProposedRelationship(
                from_index=0,
                to_index=1,
                kind=RelationshipKind.SUPPORTS,
                excerpt=segment.text,
                scope=Scope(organization="Entire Enterprise"),
            )
        ],
    )
    with pytest.raises(GroundingError, match="Relationship"):
        validate_scoped_extraction(extraction, segment, labels)


@pytest.mark.parametrize(
    "source,statement,modality",
    [
        ("Operators must not disable alarms.", "Operators must disable alarms.", "mandated"),
        ("Teams should retain logs for 30 days.", "Teams should retain logs.", "recommended"),
        ("Teams should review changes.", "Teams must review changes.", "mandated"),
    ],
)
def test_scoped_extraction_rejects_lost_qualifiers_and_invented_mandates(
    source, statement, modality
):
    segment = Segment(id=uuid4(), revision_id=uuid4(), ordinal=0, text=source)
    labels = ContentLabeling(
        labels=[ContentLabel(excerpt=source, uncertainty="Unspecified layer.")]
    )
    with pytest.raises(GroundingError):
        validate_scoped_extraction(
            Extraction(
                candidates=[
                    Candidate(
                        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
                        statement=statement,
                        excerpt=source,
                        modality=modality,
                    )
                ]
            ),
            segment,
            labels,
        )


@pytest.mark.parametrize("parameter", ["max_tokens", "max_completion_tokens"])
async def test_framework_uses_real_chat_transport_and_preserves_provider_options(parameter):
    pytest.importorskip("agent_framework_openai")
    httpx2 = pytest.importorskip("httpx2")
    from openai import AsyncOpenAI

    segment, labels, extraction = scoped_input()
    bodies = []

    def handle(request):
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        bodies.append(body)
        assert body[parameter] == 2048 and "temperature" not in body
        other = "max_tokens" if parameter == "max_completion_tokens" else "max_completion_tokens"
        assert other not in body and "tools" not in body
        return httpx2.Response(
            200,
            json={
                "id": "test-response",
                "created": 0,
                "object": "chat.completion",
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": (
                                labels.model_dump_json()
                                if body["model"] == "labeler"
                                else extraction.model_dump_json()
                            ),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        sdk = AsyncOpenAI(
            api_key="synthetic",
            base_url="https://example.test/v1",
            http_client=client,
            max_retries=0,
        )
        adapter = AgentFrameworkInference(
            Settings(
                model_id="extractor",
                labeling_model_id="labeler",
                model_temperature=None,
                model_max_token_parameter=parameter,
            ),
            sdk_client=sdk,
        )
        try:
            result = await adapter.extract(segment)
        finally:
            await adapter.close()
        assert not client.is_closed
    assert len(bodies) == 2 and result.extraction == extraction
    assert result.usage.input_tokens == 20


async def test_label_checkpoint_survives_extraction_failure_and_extractor_change():
    segment, _, _ = scoped_input()
    store, uow = MagicMock(), MagicMock()
    store.return_value.__aenter__ = AsyncMock(return_value=uow)
    store.return_value.__aexit__ = AsyncMock()
    cache = {}

    async def cached(segment_id, key):
        return cache.get(key)

    async def save(segment_id, key, result):
        cache[key] = result

    uow.sources.segments = AsyncMock(return_value=[segment])
    uow.sources.save_publication = AsyncMock()
    uow.processing.cached = AsyncMock(side_effect=cached)
    uow.processing.cache = AsyncMock(side_effect=save)
    uow.processing.heartbeat = AsyncMock()
    uow.processing.accumulate_usage = AsyncMock()
    uow.knowledge.persist = AsyncMock(return_value=[uuid4()])
    uow.commit = AsyncMock()

    class Interrupted(RulesInference):
        label_calls = 0
        extraction_calls = 0

        async def label(self, segment):
            self.label_calls += 1
            return await super().label(segment)

        async def extract_scoped(self, segment, labeling):
            self.extraction_calls += 1
            if self.extraction_calls == 1:
                raise RuntimeError("Interrupted extraction")
            return await super().extract_scoped(segment, labeling)

    inference = Interrupted()
    pipeline = IngestionPipeline(store, MagicMock(), StructuredParser(), inference)
    item = SourceItem(
        id=uuid4(), connector="test", location="urn:test:scope", name="Scope", format="txt"
    )
    revision = SourceRevision(
        id=segment.revision_id, source_id=item.id, content_hash="test", artifact_key="test"
    )
    args = item, revision, None, uuid4(), uuid4()
    with pytest.raises(RuntimeError, match="Interrupted"):
        await pipeline._execute(*args)
    assert len(cache) == 1 and next(iter(cache.values())).labeling
    await pipeline._execute(*args)
    await pipeline._execute(*args)
    assert inference.label_calls == 1 and inference.extraction_calls == 2
    inference.extraction_fingerprint = "updated-extraction-contract"
    pipeline = IngestionPipeline(store, MagicMock(), StructuredParser(), inference)
    await pipeline._execute(*args)
    assert inference.label_calls == 1 and inference.extraction_calls == 3


async def test_unsupported_files_are_inventoried_without_fetching(tmp_path):
    (tmp_path / "policy.txt").write_text("Users must authenticate.")
    (tmp_path / "image.bin").write_bytes(b"not a supported document")
    items = [item async for item in FilesystemConnector(tmp_path).discover()]
    assert len(items) == 2
    assert next(item for item in items if item.name == "image.bin").metadata["metadata_only"]
    assert "metadata_only" not in next(item for item in items if item.name == "policy.txt").metadata


async def test_default_scan_finishes_inventory_before_semantic_processing(tmp_path, monkeypatch):
    for index in range(3):
        (tmp_path / f"source-{index}.txt").write_text("Users must authenticate.")
    store, uow = MagicMock(), MagicMock()
    store.return_value.__aenter__ = AsyncMock(return_value=uow)
    store.return_value.__aexit__ = AsyncMock()
    uow.processing.save_scan = AsyncMock()
    uow.sources.list = AsyncMock(return_value=[])
    uow.commit = AsyncMock()
    pipeline = IngestionPipeline(store, MagicMock(), StructuredParser(), RulesInference())
    prepared = []

    async def prepare(connector, item, dry_run, notify):
        prepared.append(item.id)
        return (
            SourceRevision(id=uuid4(), source_id=item.id, content_hash="test", artifact_key="test"),
            None,
            True,
        )

    async def ingest(item, revision, content):
        assert len(prepared) == 3
        return "processed", 1

    monkeypatch.setattr(pipeline, "_prepare", prepare)
    monkeypatch.setattr(pipeline, "_ingest", ingest)
    report = await pipeline.scan([FilesystemConnector(tmp_path)])
    assert report.processed == 3 and not report.failed


async def test_skill_evaluation_is_split_isolated_independent_and_not_offline_qualification(
    tmp_path,
):
    dataset = SkillDataset.model_validate_json(DATASET.read_bytes())
    raw = json.loads(DATASET.read_bytes())
    raw["cases"][-1]["family"] = raw["cases"][0]["family"]
    with pytest.raises(ValidationError, match="cross splits"):
        SkillDataset.model_validate(raw)

    class IndependentExtractor(RulesInference):
        async def label(self, segment):
            raise AssertionError("Extraction evaluation must use gold routing, not invoke labeling")

        async def extract_scoped(self, segment, labeling):
            case = next(case for case in dataset.cases if case.text == segment.text)
            return InferenceResult(
                extraction=case.expected,
                model_id="synthetic",
                prompt_version="synthetic",
                usage=Usage(input_tokens=10, output_tokens=5, cost=0.001),
            )

    report = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "skills.json",
        IndependentExtractor(),
        skill="extraction",
    )
    assert report["passed"] and set(report["skills"]) == {"extraction"}
    assert report["skills"]["extraction"]["metrics"]["precision"] == 1
    assert not report["skills"]["extraction"]["qualified"]
    with pytest.raises(ValueError, match="budget"):
        await evaluate_skills(
            DATASET, GATES, tmp_path / "too-many.json", RulesInference(), max_cases=1
        )


async def test_live_skill_gates_require_priced_usage_and_preserve_attribution(tmp_path):
    dataset = SkillDataset.model_validate_json(DATASET.read_bytes())
    teacher = load_dataset(Path("evals") / "teacher-v2.json")
    sources = {source.id: source for source in teacher.sources}
    for case in dataset.cases:
        if case.source:
            assert case.source == sources[case.source.id]
            assert any(
                seed.source == case.source.id and seed.quote == case.text for seed in teacher.seeds
            )

    class Labeler(RulesInference):
        capabilities = Capabilities(model_id="synthetic-model")
        priced = True

        async def label(self, segment):
            case = next(case for case in dataset.cases if case.text == segment.text)
            result = await super().label(segment)
            return result.model_copy(
                update={
                    "labeling": case.labeling,
                    "usage": Usage(
                        input_tokens=10,
                        output_tokens=5,
                        cost=0.001 if self.priced else None,
                    ),
                }
            )

    labeler = Labeler()
    report = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "priced.json",
        labeler,
        skill="labeling",
        live=True,
    )
    assert report["passed"] and report["skills"]["labeling"]["qualified"]
    labeler.priced = False
    unknown = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "unpriced.json",
        labeler,
        skill="labeling",
        live=True,
    )
    assert not unknown["passed"]
    assert not unknown["skills"]["labeling"]["quality_gates"]["priced_cost"]


def test_document_metrics_separate_company_coverage_layers_and_named_scope():
    dataset = SkillDataset.model_validate_json(DOCUMENTS.read_bytes())
    cases = list(dataset.cases)
    predictions = [case.labeling for case in cases]
    perfect = document_metrics(cases, predictions)
    assert perfect["coverage"]["accuracy"] == 1
    assert perfect["ontology_type_set_accuracy"] == 1

    nist = predictions[1]
    predictions[1] = nist.model_copy(
        update={
            "coverage": None,
            "labels": [
                label.model_copy(update={"scope": Scope(organization="NIST")})
                for label in nist.labels
                if label.semantic_type != SemanticType.TECHNICAL_REQUIREMENT
            ],
        }
    )
    report = document_metrics(cases, predictions)
    assert report["coverage"]["correct"] == 2
    assert report["documents"][1]["missing_ontology_types"] == ["TechnicalRequirement"]
    assert report["documents"][1]["expected_scopes"] == []
    assert report["documents"][1]["actual_scopes"] == [{"organization": "NIST"}]
    assert report["documents"][2]["ontology_types_exact_match"]
    assert label_metrics(cases, predictions)["fp"] > 0


async def test_document_eval_uses_only_source_text_and_keeps_iso_negative(tmp_path):
    dataset = SkillDataset.model_validate_json(DOCUMENTS.read_bytes())

    class Reader(RulesInference):
        capabilities = Capabilities(model_id="synthetic-reader")

        async def label(self, segment):
            case = next(case for case in dataset.cases if case.text == segment.text)
            assert not segment.location
            return LabelingResult(
                labeling=case.labeling,
                model_id="synthetic",
                prompt_version="synthetic",
                usage=Usage(input_tokens=10, output_tokens=5, model_calls=1, cost=0.001),
            )

        async def extract_scoped(self, segment, labeling):
            case = next(case for case in dataset.cases if case.text == segment.text)
            assert labeling == case.labeling
            return InferenceResult(
                extraction=case.expected,
                model_id="synthetic",
                prompt_version="synthetic",
                usage=Usage(input_tokens=10, output_tokens=5, model_calls=1, cost=0.001),
            )

    report = await evaluate_skills(
        DOCUMENTS, GATES, tmp_path / "documents.json", Reader(), split="all"
    )
    assert report["skills"]["labeling"]["document_classification"]["coverage"]["accuracy"] == 1
    assert report["skills"]["extraction"]["metrics"]["recall"] == 1
    assert report["skills"]["extraction"]["negative_cases"] == {
        "scored": 1,
        "correctly_empty": 1,
    }
    assert not any(skill["qualified"] for skill in report["skills"].values())

    class MissingCoverage(Reader):
        async def label(self, segment):
            result = await super().label(segment)
            return result.model_copy(
                update={"labeling": result.labeling.model_copy(update={"coverage": None})}
            )

    missing = await evaluate_skills(
        DOCUMENTS, GATES, tmp_path / "missing.json", MissingCoverage(), split="all", live=True
    )
    assert missing["skills"]["labeling"]["metrics"]["recall"] == 1
    assert not missing["skills"]["labeling"]["quality_gates"]["document_coverage"]
    assert not missing["passed"]


def test_coverage_and_document_annotations_must_resolve_to_source_passages():
    segment, labeling, _ = scoped_input()
    forged = labeling.model_copy(
        update={"coverage": ContentCoverage(level="company_level", excerpt="Unseen company")}
    )
    with pytest.raises(GroundingError, match="Coverage evidence"):
        validate_labeling(forged, segment)
    with pytest.raises(ValidationError, match="explanation"):
        ContentCoverage(level="unknown", excerpt=segment.text)
    raw = json.loads(DOCUMENTS.read_bytes())
    raw["cases"][0]["document"]["passages"][0]["excerpt"] = "Fabricated quote."
    with pytest.raises(ValidationError, match="selected source passages"):
        SkillDataset.model_validate(raw)


def test_document_source_audit_checks_hash_and_exact_pdf_page(tmp_path, monkeypatch):
    dataset = SkillDataset.model_validate_json(DOCUMENTS.read_bytes())
    case = dataset.cases[1]
    assert case.source and case.document
    passage = case.document.passages[0].model_copy(update={"pdf_page": 2})
    monkeypatch.chdir(tmp_path)
    path = Path("source.pdf")
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30, 30), passage.excerpt, fontsize=6)
        pdf.new_page().insert_text((30, 30), "Unrelated page.")
        pdf.save(path)
    sample = case.document.model_copy(update={"retained_path": path, "passages": (passage,)})
    source = case.source.model_copy(update={"sha256": content_hash(path.read_bytes())})
    selected = case.model_copy(update={"document": sample, "source": source})
    with pytest.raises(ValueError, match="cited page"):
        _audit_document_sources([selected])
    sample = sample.model_copy(update={"passages": (passage.model_copy(update={"pdf_page": 1}),)})
    selected = selected.model_copy(update={"document": sample})
    assert _audit_document_sources([selected])["passages"] == 1
    path.write_bytes(b"Changed original")
    with pytest.raises(ValueError, match="hash changed"):
        _audit_document_sources([selected])


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_metadata_source_identity_is_checkout_line_ending_independent(
    tmp_path, monkeypatch, newline
):
    case = SkillDataset.model_validate_json(DOCUMENTS.read_bytes()).cases[2]
    assert case.source and case.document
    monkeypatch.chdir(tmp_path)
    path = Path("metadata.md")
    path.write_bytes(case.text.replace("\n", newline).encode())
    sample = case.document.model_copy(update={"retained_path": path})
    source = case.source.model_copy(update={"sha256": content_hash(case.text.encode())})
    selected = case.model_copy(update={"document": sample, "source": source})
    assert _audit_document_sources([selected])["passages"] == 2
