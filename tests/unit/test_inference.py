import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from ekb.adapters.inference import AgentFrameworkInference, OpenAIInference
from ekb.config import Settings
from ekb.domain.models import Candidate, ConceptKind, Extraction, Modality, Segment


def segment():
    return Segment(
        id=uuid4(), revision_id=uuid4(), ordinal=0, text="Operators must not disable alarms."
    )


def output(excerpt):
    return Extraction(
        candidates=[
            Candidate(
                kind=ConceptKind.REQUIREMENT,
                statement=excerpt,
                excerpt=excerpt,
            )
        ]
    ).model_dump_json()


async def test_model_validation_retry_and_usage_accounting():
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        text = output("Invented evidence") if len(calls) == 1 else output(segment().text)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    settings = Settings(
        model_id="configured-small-model",
        model_structured_output=True,
        model_input_price=1,
        model_output_price=2,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = OpenAIInference(settings, client=client)
        result = await adapter.extract(segment())
    assert len(calls) == 2
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert result.usage.input_tokens == 20 and result.usage.output_tokens == 10
    assert result.usage.validation_failures == 1
    assert result.usage.cost == pytest.approx(0.00004)


async def test_invalid_model_output_is_rejected_after_bounded_attempts():
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    settings = Settings(model_id="test", model_retries=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="2 validation attempts"):
            await OpenAIInference(settings, client=client).extract(segment())
    assert calls == 2


async def test_reasoning_model_can_omit_temperature_and_select_completion_budget():
    def handle(request):
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 1024
        assert "max_tokens" not in body and "temperature" not in body
        return httpx.Response(
            200, json={"choices": [{"message": {"content": output(segment().text)}}]}
        )

    settings = Settings(
        model_id="configured-reasoning-deployment",
        model_temperature=None,
        model_max_token_parameter="max_completion_tokens",
        model_max_output_tokens=1024,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await OpenAIInference(settings, client=client).extract(segment())


async def test_null_temperature_environment_and_verifier_cache_identity(monkeypatch):
    from ekb.adapters.inference.reconciliation import OpenAISemanticVerifier

    monkeypatch.setenv("EKB_MODEL_TEMPERATURE", "null")
    assert Settings().model_temperature is None
    settings = Settings(reconciliation_model_id="configured", model_temperature=0)
    original = OpenAISemanticVerifier(settings)
    changed = OpenAISemanticVerifier(settings.model_copy(update={"model_temperature": None}))
    try:
        assert original.fingerprint != changed.fingerprint
    finally:
        await original.close()
        await changed.close()


async def test_debug_is_opt_in_and_payloads_are_separate_from_telemetry(tmp_path):
    def handle(request):
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": output(segment().text)}}]}
        )

    settings = Settings(
        model_id="test",
        data_dir=tmp_path,
        debug_artifacts=True,
        model_api_key=SecretStr("test-secret"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await OpenAIInference(settings, client=client).extract(segment())
    artifacts = list((tmp_path / "debug").glob("*.json"))
    assert len(artifacts) == 1
    text = artifacts[0].read_text()
    assert "must not disable" in text and "test-secret" not in text
    assert "trace_id" in json.loads(text)


async def test_optional_agent_framework_uses_verified_chat_api(monkeypatch):
    pytest.importorskip("agent_framework_openai")
    from agent_framework import ChatResponse, Message
    from agent_framework.openai import OpenAIChatClient

    async def fake_response(self, messages, *, options):
        assert isinstance(messages[0], Message)
        assert messages[1].role == "user"
        assert options["max_tokens"] == 2048 and options["temperature"] == 0
        return ChatResponse(messages=[Message(role="assistant", contents=[output(segment().text)])])

    monkeypatch.setattr(OpenAIChatClient, "get_response", fake_response)
    adapter = AgentFrameworkInference(Settings(model_id="test"))
    try:
        result = await adapter.extract(segment())
        assert result.extraction.candidates[0].excerpt == segment().text
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "original,modality,rewrite",
    [
        (
            "Operators must not disable alarms unless a permit exists.",
            "mandated",
            "Operators must disable alarms.",
        ),
        (
            "Teams should retain logs for 30 days.",
            "recommended",
            "Teams must retain logs for 30 days.",
        ),
        ("Teams should retain logs for 30 days.", "recommended", "Teams should retain logs."),
        ("We aim to reduce emissions.", "unspecified", "The organization must reduce emissions."),
        ("Never disclose credentials.", "unspecified", "Disclose credentials."),
        (
            "Reveal errors only to {{ insert: param, si-11_odp }}.",
            "unspecified",
            "Reveal errors only to {{ insert: param, si-11_odp }}.",
        ),
    ],
)
def test_assertion_qualifier_gates_reject_changed_meaning(original, modality, rewrite):
    from ekb.domain.assertions import validate_assertion
    from ekb.domain.models import AssertionDraft

    candidate = Candidate(
        kind=ConceptKind.REQUIREMENT,
        statement=original,
        excerpt=original,
        modality=Modality(modality),
    )
    with pytest.raises(ValueError):
        validate_assertion(candidate, AssertionDraft(text=rewrite))


@pytest.mark.parametrize("valid_on_retry", [True, False])
async def test_assertion_writer_retries_unsafe_phrasing_and_accounts_for_usage(valid_on_retry):
    from ekb.adapters.inference.assertions import OpenAIAssertionWriter
    from ekb.domain.models import (
        ConceptVersion,
        Occurrence,
        Provenance,
        SourceItem,
        SourceRecord,
        SourceRevision,
    )

    source_segment = segment()
    version = ConceptVersion(
        id=uuid4(),
        concept_id=uuid4(),
        candidate=Candidate(
            kind=ConceptKind.REQUIREMENT,
            statement=source_segment.text.replace("must not", "must\nnot"),
            excerpt=source_segment.text,
            modality=Modality.MANDATED,
        ),
    )
    evidence = Provenance(
        occurrence=Occurrence(
            id=uuid4(),
            concept_version_id=version.id,
            segment_id=source_segment.id,
            excerpt=source_segment.text,
            start=0,
            end=len(source_segment.text),
        ),
        segment=source_segment,
        revision=SourceRevision(
            id=source_segment.revision_id,
            source_id=uuid4(),
            content_hash="test",
            artifact_key="test",
        ),
        source=SourceRecord(
            item=SourceItem(
                id=uuid4(), connector="test", location="urn:test", name="Safety guide", format="txt"
            )
        ),
    )
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        value = (
            source_segment.text
            if valid_on_retry and len(calls) > 1
            else "Operators must disable alarms."
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"text": value, "uncertainty": None})}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        writer = OpenAIAssertionWriter(
            Settings(model_id="test", model_context_tokens=20000), client=client
        )
        assertion = await writer.rewrite(version, evidence)
    expected_calls = 2 if valid_on_retry else 3
    assert len(calls) == expected_calls
    assert assertion.text == (source_segment.text if valid_on_retry else None)
    if not valid_on_retry:
        assert assertion.uncertainty
    assert assertion.occurrence_id == evidence.occurrence.id
    assert assertion.usage.input_tokens == expected_calls * 10
    assert assertion.usage.validation_failures == (1 if valid_on_retry else 3)
    assert version.candidate.statement == source_segment.text.replace("must not", "must\nnot")


@pytest.mark.parametrize(
    "text,accepted",
    [
        ("Operators must not disable alarms.", True),
        ("It must remain our number one priority.", False),
        ("Operators must retain logs. They should review them.", False),
        ("Operators must retain logs\nfor review.", False),
    ],
)
def test_existing_assertions_reuse_only_clear_single_sentence_wording(text, accepted):
    from ekb.domain.assertions import existing_assertion

    candidate = Candidate(kind=ConceptKind.REQUIREMENT, statement=text, excerpt=text)
    assert (existing_assertion(candidate) == text) is accepted
