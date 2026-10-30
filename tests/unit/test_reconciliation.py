import json
from uuid import uuid4

import httpx
import pytest

from ekb.adapters.inference.reconciliation import OpenAIEmbeddings, OpenAISemanticVerifier
from ekb.config import Settings
from ekb.domain.models import Scope, SemanticType
from ekb.domain.reconciliation import (
    EmbeddingResult,
    SemanticAssertion,
    Verification,
    structural_verification,
)
from ekb.services.retrieval import cosine


def view(text):
    return SemanticAssertion(
        occurrence_id=uuid4(),
        statement=text,
        excerpt=text,
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
        scope=Scope(platform="example"),
    )


def test_exact_core_is_not_namespace_identity_or_canonical_adoption():
    old = view("Protect applications.")
    new = view("Protect applications.")
    result = structural_verification(old, new)
    assert result.judgment.outcome == "equivalent"
    assert result.judgment.change_class is None
    assert result.method == "structural_rules"
    other_scope = new.model_copy(update={"scope": Scope(platform="AWS")})
    assert structural_verification(old, other_scope).judgment.outcome == "distinct"
    assert structural_verification(old, old).judgment.outcome == "exact_duplicate"
    unknown_scope = new.model_copy(update={"scope": Scope()})
    assert structural_verification(old, unknown_scope).judgment.outcome == "unknown"
    unknown_type = new.model_copy(update={"semantic_type": None})
    assert structural_verification(old, unknown_type).judgment.outcome == "unknown"


def test_changed_negation_is_not_equivalence_or_a_minor_change():
    old = view("Allow public application traffic.")
    new = view("Do not allow public application traffic.")
    result = structural_verification(old, new)
    assert result.judgment.outcome == "potential_conflict"
    assert result.judgment.change_class == "MAJOR"


def test_cosine_handles_extreme_finite_vector_magnitudes():
    assert cosine((1e308, 1e308), (1e-300, 1e-300)) == pytest.approx(1.0)


async def test_small_model_rejects_invented_evidence_and_counts_validation_calls():
    old, new = (
        view("Protect applications."),
        view("Protect applications and APIs."),
    )
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["model"] == "test-small"
        assert body["response_format"]["json_schema"]["name"] == "semanticjudgment"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "outcome": "compatible_extension",
                                    "confidence": 0.9,
                                    "rationale": "Synthetic test response.",
                                    "old_excerpt": "Invented"
                                    if len(calls) == 1
                                    else "Protect applications.",
                                    "new_excerpt": "Protect applications and APIs.",
                                    "change_class": "MINOR",
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        verifier = OpenAISemanticVerifier(
            Settings(
                reconciliation_model_id="test-small",
                model_structured_output=True,
                model_input_price=1.0,
                model_output_price=2.0,
            ),
            client=client,
        )
        result = await verifier.verify(old, new)
    assert result.method == "small_model" and result.model_calls == 2
    assert result.status == "completed" and result.completed
    assert result.usage.input_tokens == 20 and result.usage.validation_failures == 1
    assert result.usage.cost == pytest.approx(0.00004)
    assert result.judgment.change_class == "MINOR"


async def test_invalid_small_model_result_remains_explicitly_unknown():
    def handle(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        verifier = OpenAISemanticVerifier(
            Settings(reconciliation_model_id="test", model_retries=0), client=client
        )
        result = await verifier.verify(view("Protect APIs."), view("Protect web APIs."))
    assert result.method == "model_failed"
    assert result.status == "failed" and not result.completed
    assert result.judgment.outcome == "unknown" and result.judgment.change_class is None
    assert result.model_calls == 1 and result.usage.validation_failures == 1


@pytest.mark.parametrize(
    "method,status", [("model_failed", "failed"), ("context_limit", "context_limit")]
)
def test_legacy_incomplete_methods_cannot_claim_completion(method, status):
    judgment = structural_verification(view("Protect APIs."), view("Protect web APIs.")).judgment
    verification = Verification(method=method, status="completed", judgment=judgment)
    assert verification.status == status and not verification.completed


async def test_verifier_reports_context_limit_without_calling_provider():
    def handle(request):
        raise AssertionError("Context limit must be detected before a provider call")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        verifier = OpenAISemanticVerifier(
            Settings(reconciliation_model_id="test", model_context_tokens=512), client=client
        )
        result = await verifier.verify(view("Protect APIs."), view("Protect web APIs."))
    assert result.method == result.status == "context_limit"
    assert not result.completed and result.model_calls == 0


@pytest.mark.parametrize("price", [None, 2.0])
async def test_embeddings_are_batched_ordered_and_validate_dimensions(price):
    sizes = []

    def handle(request):
        body = json.loads(request.content)
        sizes.append(len(body["input"]))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0, float(i)]} for i in reversed(range(sizes[-1]))
                ],
                "usage": {"prompt_tokens": sizes[-1]},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = OpenAIEmbeddings(
            Settings(embedding_model_id="test", embedding_input_price=price), client=client
        )
        result = await adapter.embed(["Synthetic"] * 65)
    assert sizes == [64, 1] and result.calls == 2 and result.input_tokens == 65
    assert result.usage_complete is True
    if price is None:
        assert result.cost is None
    else:
        assert result.cost == pytest.approx(0.00013)
    assert result.vectors[0] == (1, 0) and result.vectors[63] == (1, 63)

    def invalid(request):
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [1]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid)) as client:
        adapter = OpenAIEmbeddings(Settings(embedding_model_id="test"), client=client)
        with pytest.raises(ValueError, match="index"):
            await adapter.embed(["Synthetic"])


@pytest.mark.parametrize(
    "price,usage,complete",
    [
        (None, {"prompt_tokens": 1}, True),
        (2.0, {}, False),
        (2.0, {"prompt_tokens": 0}, False),
        (2.0, {"prompt_tokens": True}, False),
    ],
)
async def test_embedding_cost_stays_unknown_without_price_or_metered_tokens(price, usage, complete):
    def handle(request):
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0]}], "usage": usage}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = OpenAIEmbeddings(
            Settings(embedding_model_id="test", embedding_input_price=price), client=client
        )
        result = await adapter.embed(["Synthetic"])
    assert result.calls == 1 and result.cost is None
    assert result.usage_complete is complete


async def test_embedding_usage_completeness_for_partial_batches_and_no_calls():
    batches = 0

    def handle(request):
        nonlocal batches
        batches += 1
        count = len(json.loads(request.content)["input"])
        return httpx.Response(
            200,
            json={
                "data": [{"index": i, "embedding": [1.0]} for i in range(count)],
                "usage": {"prompt_tokens": count} if batches == 1 else {},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = OpenAIEmbeddings(
            Settings(embedding_model_id="test", embedding_input_price=2.0), client=client
        )
        partial = await adapter.embed(["Synthetic"] * 65)
        empty = await adapter.embed([])
    assert partial.calls == 2 and partial.input_tokens == 64
    assert partial.usage_complete is False and partial.cost is None
    assert empty.calls == 0 and empty.input_tokens == 0 and empty.usage_complete is True
    assert batches == 2


def test_replaceable_embedding_provider_defaults_to_unknown_usage_completeness():
    result = EmbeddingResult(vectors=((1.0,),), calls=1, input_tokens=1)
    assert result.usage_complete is None


async def test_verifier_cost_stays_unknown_without_metered_tokens():
    old, new = view("Protect APIs."), view("Protect external APIs.")
    answer = {
        "outcome": "equivalent",
        "confidence": 0.9,
        "rationale": "Grounded synthetic result.",
        "old_excerpt": old.excerpt,
        "new_excerpt": new.excerpt,
    }

    def handle(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        verifier = OpenAISemanticVerifier(
            Settings(
                reconciliation_model_id="test",
                model_input_price=1.0,
                model_output_price=2.0,
            ),
            client=client,
        )
        result = await verifier.verify(old, new)
    assert result.method == "small_model" and result.model_calls == 1
    assert result.usage.cost is None


@pytest.mark.parametrize("kind", ["embedding", "verification"])
async def test_retry_without_usage_does_not_report_partial_cost(kind, monkeypatch):
    old, new = view("Protect APIs."), view("Protect external APIs.")
    attempts = 0

    async def immediate_retry(operation):
        try:
            return await operation()
        except httpx.HTTPStatusError:
            return await operation()

    monkeypatch.setattr("ekb.adapters.inference.reconciliation.retry", immediate_retry)

    def handle(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        if kind == "embedding":
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [1.0]}], "usage": {"prompt_tokens": 1}},
            )
        answer = {
            "outcome": "equivalent",
            "confidence": 0.9,
            "rationale": "Grounded synthetic result.",
            "old_excerpt": old.excerpt,
            "new_excerpt": new.excerpt,
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(answer)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    settings = Settings(
        embedding_model_id="test",
        reconciliation_model_id="test",
        embedding_input_price=1.0,
        model_input_price=1.0,
        model_output_price=2.0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        if kind == "embedding":
            result = await OpenAIEmbeddings(settings, client=client).embed(["Synthetic"])
            assert result.calls == 2 and result.cost is None
            assert result.usage_complete is False
        else:
            verification = await OpenAISemanticVerifier(settings, client=client).verify(old, new)
            assert verification.model_calls == 2 and verification.usage.cost is None
