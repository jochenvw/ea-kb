import json
from uuid import uuid4

import httpx
import pytest

from ekb.adapters.inference.reconciliation import OpenAIEmbeddings, OpenAISemanticVerifier
from ekb.adapters.parsers.benchmarks import normalize_benchmark
from ekb.adapters.sources.benchmarks import EDITIONS, LEARN, _asset
from ekb.config import Settings
from ekb.domain.models import Scope, now
from ekb.domain.reconciliation import ControlView, structural_verification


def view(edition, text, title="Protect applications"):
    body = f"# Synthetic\n\n## NS-6: {title}\n\n### Security principle\n\n{text}\n"
    document = normalize_benchmark(
        _asset(edition, LEARN + edition, "domain", domain="NS"), body.encode(), EDITIONS[edition]
    )
    return ControlView(
        snapshot_id=uuid4(),
        revision_id=uuid4(),
        segment_id=document.segment_id,
        retrieved_at=now(),
        control=document.controls[0],
    )


def test_exact_core_is_not_namespace_identity_or_canonical_adoption():
    old = view("mcsb-v1", "Protect applications.")
    new = view("mcsb-v2", "Protect applications.")
    result = structural_verification(old, new)
    assert result.judgment.outcome == "equivalent"
    assert result.judgment.change_class is None
    assert result.method == "structural_rules"
    other_scope = new.model_copy(update={"scope": Scope(platform="AWS")})
    assert structural_verification(old, other_scope).judgment.outcome == "distinct"
    assert structural_verification(old, old).judgment.outcome == "exact_duplicate"


def test_changed_negation_is_not_equivalence_or_a_minor_change():
    old = view("mcsb-v1", "Allow public application traffic.")
    new = view("mcsb-v2", "Do not allow public application traffic.")
    result = structural_verification(old, new)
    assert result.judgment.outcome == "potential_conflict"
    assert result.judgment.change_class == "MAJOR"


async def test_small_model_rejects_invented_evidence_and_counts_validation_calls():
    old, new = (
        view("mcsb-v1", "Protect applications."),
        view("mcsb-v2", "Protect applications and APIs."),
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
            Settings(reconciliation_model_id="test-small", model_structured_output=True),
            client=client,
        )
        result = await verifier.verify(old, new)
    assert result.method == "small_model" and result.model_calls == 2
    assert result.usage.input_tokens == 20 and result.usage.validation_failures == 1
    assert result.judgment.change_class == "MINOR"


async def test_invalid_small_model_result_remains_explicitly_unknown():
    def handle(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        verifier = OpenAISemanticVerifier(
            Settings(reconciliation_model_id="test", model_retries=0), client=client
        )
        result = await verifier.verify(
            view("mcsb-v1", "Protect APIs."), view("mcsb-v2", "Protect web APIs.")
        )
    assert result.method == "model_failed"
    assert result.judgment.outcome == "unknown" and result.judgment.change_class is None
    assert result.model_calls == 1 and result.usage.validation_failures == 1


async def test_embeddings_are_batched_ordered_and_validate_dimensions():
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
        adapter = OpenAIEmbeddings(Settings(embedding_model_id="test"), client=client)
        result = await adapter.embed(["Synthetic"] * 65)
    assert sizes == [64, 1] and result.calls == 2 and result.input_tokens == 65
    assert result.vectors[0] == (1, 0) and result.vectors[63] == (1, 63)

    def invalid(request):
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [1]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid)) as client:
        adapter = OpenAIEmbeddings(Settings(embedding_model_id="test"), client=client)
        with pytest.raises(ValueError, match="index"):
            await adapter.embed(["Synthetic"])
