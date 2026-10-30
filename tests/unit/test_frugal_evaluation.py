import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from pydantic_evals import Dataset as EvalDataset
from typer.testing import CliRunner

from ekb.adapters.artifacts import FilesystemArtifactStore
from ekb.adapters.inference import RulesInference
from ekb.adapters.inference.reconciliation import OpenAIEmbeddings
from ekb.config import Settings
from ekb.domain.models import (
    Candidate,
    Capabilities,
    Extraction,
    InferenceResult,
    Scope,
    Usage,
    content_hash,
)
from ekb.domain.reconciliation import (
    EmbeddingResult,
    SemanticJudgment,
    SemanticOutcome,
    Verification,
)
from ekb.evaluation import frugal, suite
from ekb.evaluation.dataset import TeacherDataset, extraction_cases, load_dataset, pair_cases
from ekb.evaluation.distillation import export_training
from ekb.interfaces.cli import app
from ekb.services.retrieval import cosine, nearest, vector_pairs

DATASET = Path("evals") / "teacher-v2.json"
GATES = Path("evals") / "gates.json"


@pytest.mark.parametrize("field", ["group", "url", "sha256"])
def test_related_source_leakage_is_rejected(field):
    raw = json.loads(DATASET.read_bytes())
    train = next(s for s in raw["sources"] if s["split"] == "train")
    test = next(s for s in raw["sources"] if s["split"] == "test")
    train[field] = test[field]
    with pytest.raises(ValidationError, match="one split"):
        TeacherDataset.model_validate(raw)


def test_empty_seed_split_and_cross_split_text_are_rejected():
    raw = json.loads(DATASET.read_bytes())
    training = {s["id"] for s in raw["sources"] if s["split"] == "train"}
    raw["seeds"] = [s for s in raw["seeds"] if s["source"] not in training]
    with pytest.raises(ValidationError, match="nonempty"):
        TeacherDataset.model_validate(raw)
    raw = json.loads(DATASET.read_bytes())
    raw["seeds"][-1]["query"] = raw["seeds"][0]["quote"].upper()
    with pytest.raises(ValidationError, match="leaks"):
        TeacherDataset.model_validate(raw)


@pytest.mark.parametrize("field", ["relevant_seed_ids", "distinct_seed_ids"])
def test_evaluation_seed_links_require_existing_same_split_targets(field):
    raw = json.loads(DATASET.read_bytes())
    sources = {source["id"]: source for source in raw["sources"]}
    source = next(seed for seed in raw["seeds"] if sources[seed["source"]]["split"] == "test")
    other = next(seed for seed in raw["seeds"] if sources[seed["source"]]["split"] == "train")
    source[field] = [other["id"]]
    with pytest.raises(ValidationError, match="stay in one split"):
        TeacherDataset.model_validate(raw)
    source[field] = ["not-a-seed"]
    with pytest.raises(ValidationError, match="documented seed"):
        TeacherDataset.model_validate(raw)


async def test_dataset_annotations_drive_retrieval_relevance_and_distinct_pairs():
    dataset = load_dataset(DATASET)
    seeds = dataset.selected("test")
    by_id = {seed.id: seed for seed in seeds}
    pairs = {case_id: outcome for case_id, _, _, outcome in pair_cases(dataset, "test")}
    for seed in seeds:
        for other in seed.distinct_seed_ids:
            assert pairs[f"{seed.id}/{other}"] == "distinct"
    report = await frugal.retrieval(dataset, "test", 3, None)
    for query in report["queries"]:
        seed = by_id[query["id"]]
        assert set(query["expected"]) == {seed.id, *seed.relevant_seed_ids}


async def test_retrieval_evaluator_preserves_partial_recall_and_rank(monkeypatch):
    dataset = load_dataset(DATASET)
    seeds = dataset.selected("test")
    target = next(seed for seed in seeds if seed.relevant_seed_ids)
    unrelated = next(
        seed.id for seed in seeds if seed.id not in {target.id, *target.relevant_seed_ids}
    )
    monkeypatch.setattr(
        frugal,
        "nearest",
        lambda *_: [(unrelated, 1.0), (target.id, 0.5)],
    )
    report = await frugal.retrieval(dataset, "test", 2, Embeddings())
    query = next(row for row in report["queries"] if row["id"] == target.id)
    assert query["retrieved"] == [unrelated, target.id]
    assert query["recall"] == query["scores"]["recall"]["value"] == 0.5
    assert query["reciprocal_rank"] == query["scores"]["reciprocal_rank"]["value"] == 0.5


def test_actual_quotations_are_distinct_from_scoped_and_adversarial_variants():
    dataset = load_dataset(DATASET)
    cases = extraction_cases(dataset, "test")
    for seed in dataset.selected("test"):
        variants = {c.id.rsplit("/", 1)[1]: c for c in cases if c.id.startswith(seed.id + "/")}
        assert variants["source"].text == seed.quote
        assert not variants["source"].location["synthetic"]
        assert variants["negation"].expected[0].modality == "mandated"
        assert "must not" in variants["negation"].text
        assert variants["scope"].expected[0].scope == {"platform": "Azure"}
        assert variants["objective"].expected[0].semantic_type == "StrategicGoal"
        assert variants["injection"].expected[0].excerpt == seed.quote
        assert "approved enterprise policy" in variants["injection"].text
        assert variants["injection"].expected[0].semantic_type == seed.semantic_type
        assert variants["injection"].expected[0].modality == seed.modality


def test_v2_preserves_source_assertion_types_and_broadens_holdout_families():
    dataset = load_dataset(DATASET)
    assert dataset.version == "teacher-v2"
    assert len(dataset.sources) == 10
    assert len(dataset.seeds) == 18
    sources = {source.id: source for source in dataset.sources}
    seeds = {seed.id: seed for seed in dataset.seeds}
    assert sources["shell-strategy"].split == "validation"
    assert sources["nist-csf"].split == sources["cfr-250-107"].split == "test"

    cases = {
        case.id: case
        for split in ("validation", "test")
        for case in extraction_cases(dataset, split)
    }
    objective = cases["shell-liquids-goal/source"].expected[0]
    regulation = cases["cfr-hazard-correction/source"].expected[0]
    assert objective.semantic_type == "StrategicGoal"
    assert objective.modality == "unspecified"
    assert regulation.semantic_type is None
    assert regulation.modality == "mandated"
    assert cases["nist-csf-mission/source"].expected[0].semantic_type is None
    assert seeds["shell-liquids-goal"].teacher_model == "Copilot Auto (model identity unavailable)"
    assert not any(
        pair_id.startswith("shell-liquids-goal/")
        for pair_id, *_ in pair_cases(dataset, "validation")
    )


def student():
    cases = extraction_cases(load_dataset(DATASET), "test")
    predictions = {
        c.text: Extraction(
            candidates=[
                Candidate(
                    semantic_type=e.semantic_type,
                    statement=e.excerpt,
                    excerpt=e.excerpt,
                    modality=e.modality,
                    scope=Scope.model_validate(e.scope),
                )
                for e in c.expected
            ]
        )
        for c in cases
    }

    class Student:
        capabilities = Capabilities(model_id="synthetic-student")
        prompt_version = "synthetic-tests-only"

        async def extract(self, segment):
            return InferenceResult(
                extraction=predictions[segment.text],
                usage=Usage(input_tokens=10, output_tokens=10, cost=0.00001),
                model_id=self.capabilities.model_id,
                prompt_version=self.prompt_version,
            )

    return Student()


class Verifier:
    fingerprint = "synthetic-test-verifier"
    calls = 0

    async def verify(self, old, new):
        self.calls += 1
        expected: SemanticOutcome = next(
            outcome
            for _, a, b, outcome in pair_cases(load_dataset(DATASET), "test")
            if a.excerpt == old.excerpt
            and b.excerpt == new.excerpt
            and a.scope == old.scope
            and b.scope == new.scope
        )
        return Verification(
            method="small_model",
            model_id="synthetic-test-verifier",
            model_calls=1,
            usage=Usage(input_tokens=10, output_tokens=10, cost=0.00001),
            judgment=SemanticJudgment(
                outcome=expected,
                confidence=0.99,
                rationale="Synthetic gate regression only.",
                old_excerpt=old.excerpt,
                new_excerpt=new.excerpt,
            ),
        )


class Embeddings:
    fingerprint = "synthetic-test-embeddings"
    model_id = "synthetic-test-embeddings"

    async def embed(self, texts):
        seeds = load_dataset(DATASET).selected("test")
        ids = {s.id: i for i, s in enumerate(seeds)}
        vectors = {}
        for s in seeds:
            index = ids["ns1-waf"] if s.id in {"ns1-waf", "ns2-waf"} else ids[s.id]
            vector = tuple(float(i == index) for i in range(len(seeds)))
            vectors[s.quote] = vectors[s.query] = vector
        return EmbeddingResult(
            vectors=tuple(vectors[t] for t in texts),
            calls=1,
            input_tokens=len(texts),
            usage_complete=True,
        )


async def test_real_teacher_dataset_does_not_qualify_offline_baseline(tmp_path):
    report = await frugal.qualify(DATASET, GATES, tmp_path / "offline.json", RulesInference())
    assert report["passed"] and all(report["safety_gates"].values())
    assert not report["student_meets_provisional_gates"]
    assert not report["student_gates"]["recall"]
    assert not report["student_gates"]["retrieval_recall"]


async def test_accurate_priced_providers_pass_and_unknown_prices_do_not(tmp_path):
    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "pass.json",
        student(),
        Verifier(),
        Embeddings(),
        live=True,
        embedding_input_price=0.02,
    )
    assert report["passed"] and report["student_meets_provisional_gates"]
    assert report["recall"] == report["semantic_accuracy"] == 1
    assert report["retrieval"]["recall_at_k"] == report["retrieval"]["mrr"] == 1
    unknown = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "unknown.json",
        student(),
        Verifier(),
        Embeddings(),
        live=True,
    )
    assert not unknown["passed"] and unknown["estimated_cost"] is None
    assert not unknown["student_gates"]["priced_cost"]
    assert unknown["usage_complete"] and unknown["retrieval"]["usage_complete"] is True
    assert unknown["retrieval"]["cost"] is None


@pytest.mark.parametrize("configured_price", [None, 0.02])
async def test_qualification_preserves_complete_provider_embedding_cost(tmp_path, configured_price):
    class PricedEmbeddings(Embeddings):
        async def embed(self, texts):
            return (await super().embed(texts)).model_copy(update={"cost": 0.0007})

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "provider-cost.json",
        student(),
        Verifier(),
        PricedEmbeddings(),
        live=True,
        embedding_input_price=configured_price,
    )
    expected = (report["extraction_cases"] + report["verifier_calls"]) * 0.00001 + 0.0007
    assert report["retrieval"]["cost"] == 0.0007
    assert report["estimated_cost"] == pytest.approx(expected)
    assert report["usage_complete"] and report["student_gates"]["priced_cost"]
    assert report["passed"]


async def test_complete_multiple_embedding_calls_can_use_external_price(tmp_path):
    class BatchedEmbeddings(Embeddings):
        async def embed(self, texts):
            return (await super().embed(texts)).model_copy(update={"calls": 2})

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "external-price.json",
        student(),
        Verifier(),
        BatchedEmbeddings(),
        live=True,
        embedding_input_price=2.0,
    )
    retrieval = report["retrieval"]
    expected = (report["extraction_cases"] + report["verifier_calls"]) * 0.00001
    expected += retrieval["input_tokens"] * 2.0 / 1_000_000
    assert retrieval["calls"] == 2 and retrieval["usage_complete"] is True
    assert retrieval["cost"] is None
    assert report["estimated_cost"] == pytest.approx(expected)
    assert report["usage_complete"] and report["passed"]


@pytest.mark.parametrize(("complete", "cost"), [(None, None), (False, 0.0001)])
async def test_unverified_embedding_usage_cannot_be_priced_from_single_call(
    tmp_path, complete, cost
):
    class UnknownEmbeddings(Embeddings):
        async def embed(self, texts):
            return (await super().embed(texts)).model_copy(
                update={"usage_complete": complete, "cost": cost}
            )

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "unknown-metering.json",
        student(),
        Verifier(),
        UnknownEmbeddings(),
        live=True,
        embedding_input_price=0.02,
    )
    assert report["retrieval"]["calls"] == 1 and report["retrieval"]["input_tokens"] > 0
    assert report["retrieval"]["usage_complete"] is complete
    assert report["retrieval"]["cost"] == cost
    assert not report["usage_complete"] and report["estimated_cost"] is None
    assert not report["student_gates"]["metered_usage"]
    assert not report["student_gates"]["priced_cost"] and not report["passed"]


async def test_zero_token_embedding_call_is_incomplete_even_if_claimed_complete(tmp_path):
    class ZeroTokenEmbeddings(Embeddings):
        async def embed(self, texts):
            return (await super().embed(texts)).model_copy(update={"input_tokens": 0, "cost": 0.0})

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "zero-token-call.json",
        student(),
        Verifier(),
        ZeroTokenEmbeddings(),
        live=True,
        embedding_input_price=0.02,
    )
    assert report["retrieval"]["calls"] == 1
    assert report["retrieval"]["usage_complete"] is True
    assert report["retrieval"]["cost"] == 0.0
    assert not report["usage_complete"] and report["estimated_cost"] is None
    assert not report["student_gates"]["priced_cost"] and not report["passed"]


async def test_complete_zero_call_embedding_cache_does_not_need_a_price(tmp_path):
    class CachedEmbeddings(Embeddings):
        async def embed(self, texts):
            return (await super().embed(texts)).model_copy(update={"calls": 0, "input_tokens": 0})

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "cached-embeddings.json",
        student(),
        Verifier(),
        CachedEmbeddings(),
        live=True,
    )
    expected = (report["extraction_cases"] + report["verifier_calls"]) * 0.00001
    assert report["retrieval"]["usage_complete"] is True
    assert report["retrieval"]["calls"] == report["retrieval"]["input_tokens"] == 0
    assert report["estimated_cost"] == pytest.approx(expected)
    assert report["usage_complete"] and report["passed"]


async def test_unmetered_embedding_retry_cannot_be_priced_from_successful_response(tmp_path):
    requests = 0

    def handle(request):
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(429)
        texts = json.loads(request.content)["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [1.0, float(index + 1)]}
                    for index in range(len(texts))
                ],
                "usage": {"prompt_tokens": 42},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        embeddings = OpenAIEmbeddings(
            Settings(
                embedding_model_id="synthetic",
                embedding_input_price=0.02,
                model_endpoint="https://example.invalid",
                model_managed_identity=False,
            ),
            client=client,
        )
        try:
            report = await frugal.qualify(
                DATASET,
                GATES,
                tmp_path / "unmetered-retry.json",
                student(),
                Verifier(),
                embeddings,
                live=True,
                embedding_input_price=0.02,
            )
        finally:
            await embeddings.close()
    assert requests == report["retrieval"]["calls"] == 2
    assert report["retrieval"]["input_tokens"] == 42
    assert report["retrieval"]["usage_complete"] is False
    assert report["retrieval"]["cost"] is None
    assert not report["usage_complete"] and report["estimated_cost"] is None
    assert not report["student_gates"]["metered_usage"]
    assert not report["student_gates"]["priced_cost"] and not report["passed"]


async def test_qualification_runs_real_provider_cases_through_pydantic_evals(tmp_path, monkeypatch):
    observed = []
    original_evaluate = EvalDataset.evaluate

    async def observe(self, task, **kwargs):
        observed.append((self.name, len(self.cases), kwargs.get("max_concurrency")))
        prefix = {
            "grounded-extraction": "extraction-",
            "semantic-pairs": "semantic-",
            "embedding-batch": "embedding-",
            "retrieval-queries": "query-",
        }[self.name]
        assert all(
            case.name == case.inputs
            and case.inputs.startswith(prefix)
            and case.metadata is None
            and case.expected_output is None
            and case.evaluators
            and all(evaluator.as_spec().arguments is None for evaluator in case.evaluators)
            for case in self.cases
        )

        async def safe_task(key):
            output = await task(key)
            assert set(output.model_dump()) <= {"candidate_count", "error", "requested", "received"}
            return output

        return await original_evaluate(self, safe_task, **kwargs)

    monkeypatch.setattr(EvalDataset, "evaluate", observe)
    original_student = student()
    events = []

    class CountedStudent:
        capabilities = original_student.capabilities
        prompt_version = original_student.prompt_version

        async def extract(self, segment):
            events.append(("extract", segment.text))
            return await original_student.extract(segment)

    class CountedVerifier(Verifier):
        async def verify(self, old, new):
            events.append(("verify", old.excerpt))
            return await super().verify(old, new)

    class CountedEmbeddings(Embeddings):
        requests = 0
        texts = []

        async def embed(self, texts):
            self.requests += 1
            self.texts = list(texts)
            events.append(("embed", ""))
            return await super().embed(texts)

    verifier, embeddings = CountedVerifier(), CountedEmbeddings()
    output = tmp_path / "qualification.json"
    report = await frugal.qualify(
        DATASET,
        GATES,
        output,
        CountedStudent(),
        verifier,
        embeddings,
        live=True,
        embedding_input_price=0.02,
    )
    selected = load_dataset(DATASET).selected("test")
    expected_cases = extraction_cases(load_dataset(DATASET), "test")
    assert [text for kind, text in events if kind == "extract"] == [c.text for c in expected_cases]
    assert all(kind == "extract" for kind, _ in events[: len(expected_cases)])
    assert events[-1][0] == "embed"
    assert verifier.calls == report["verifier_calls"]
    assert embeddings.requests == report["retrieval"]["calls"] == 1
    assert embeddings.texts == list(
        dict.fromkeys([s.quote for s in selected] + [s.query for s in selected])
    )
    assert {name for name, _, _ in observed} == {
        "grounded-extraction",
        "semantic-pairs",
        "embedding-batch",
        "retrieval-queries",
    }
    assert all(concurrency == 1 for _, _, concurrency in observed)
    assert all(0 < count <= 16 for _, count, _ in observed)
    assert report["evaluation_runner"] == "pydantic-evals"
    extraction = json.loads(output.with_name("qualification-extraction.json").read_text())
    assert extraction["documents"][0]["scores"]["tp"]["value"] == 1
    assert extraction["documents"][0]["assertions"]["case_exact_match"]["value"]
    assert report["judgments"][0]["assertions"]["valid"]["value"]
    assert report["retrieval"]["queries"][0]["scores"]["recall"]["value"] == 1
    assert report["retrieval"]["embedding_evaluation"]["assertions"]["vector_count_matches"][
        "value"
    ]


async def test_false_equivalence_and_poor_retrieval_cannot_be_bought_by_low_cost(tmp_path):
    class WrongVerifier(Verifier):
        async def verify(self, old, new):
            result = await super().verify(old, new)
            return result.model_copy(
                update={"judgment": result.judgment.model_copy(update={"outcome": "equivalent"})}
            )

    class FlatEmbeddings(Embeddings):
        async def embed(self, texts):
            return EmbeddingResult(
                vectors=tuple((1.0, 0.0) for _ in texts),
                calls=1,
                input_tokens=len(texts),
                usage_complete=True,
            )

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "bad.json",
        student(),
        WrongVerifier(),
        FlatEmbeddings(),
        live=True,
        embedding_input_price=0,
    )
    assert not report["passed"]
    assert not report["safety_gates"]["no_false_equivalence"]
    assert not report["student_gates"]["retrieval_recall"]
    assert report["student_gates"]["priced_cost"]
    false_pair = next(row for row in report["judgments"] if row.get("false_equivalence"))
    assert not false_pair["assertions"]["no_false_equivalence"]["value"]
    assert not false_pair["assertions"]["correct"]["value"]
    for query in report["retrieval"]["queries"]:
        assert query["scores"]["recall"]["value"] == query["recall"]
        assert query["scores"]["reciprocal_rank"]["value"] == query["reciprocal_rank"]


async def test_ungrounded_semantic_judgment_still_fails_safety_without_losing_usage(tmp_path):
    class Ungrounded(Verifier):
        async def verify(self, old, new):
            result = await super().verify(old, new)
            return result.model_copy(
                update={
                    "judgment": result.judgment.model_copy(
                        update={"old_excerpt": "fabricated evidence"}
                    )
                }
            )

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "ungrounded.json",
        student(),
        Ungrounded(),
        Embeddings(),
        live=True,
        embedding_input_price=0,
    )
    judgment = next(row for row in report["judgments"] if row["result"]["method"] == "small_model")
    assert not judgment["valid"] and not judgment["assertions"]["valid"]["value"]
    assert not judgment["assertions"]["correct"]["value"]
    assert not report["passed"] and not report["safety_gates"]["no_provider_errors"]
    assert report["usage_complete"] and report["estimated_cost"] is not None


@pytest.mark.parametrize(
    ("status", "valid"),
    [("completed", True), ("failed", False), ("context_limit", False)],
)
async def test_verifier_status_not_adapter_method_controls_validity(tmp_path, status, valid):
    class ReplacementVerifier(Verifier):
        async def verify(self, old, new):
            result = await super().verify(old, new)
            return result.model_copy(update={"method": "replacement-adapter", "status": status})

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / f"{status}.json",
        student(),
        ReplacementVerifier(),
        Embeddings(),
        live=True,
        embedding_input_price=0.02,
    )
    replacement = [
        row for row in report["judgments"] if row["result"]["method"] == "replacement-adapter"
    ]
    assert replacement and all(row["valid"] is valid for row in replacement)
    assert all(row["assertions"]["valid"]["value"] is valid for row in replacement)
    assert report["safety_gates"]["no_provider_errors"] is valid
    assert report["passed"] is valid


async def test_missing_provider_usage_is_not_a_free_qualified_run(tmp_path):
    correct = student()

    class Unmetered:
        capabilities = correct.capabilities
        prompt_version = correct.prompt_version

        async def extract(self, segment):
            result = await correct.extract(segment)
            return result.model_copy(update={"usage": Usage(cost=0)})

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "unmetered.json",
        Unmetered(),
        Verifier(),
        Embeddings(),
        live=True,
        embedding_input_price=0,
    )
    assert report["recall"] == 1
    assert not report["usage_complete"] and report["estimated_cost"] is None
    assert not report["passed"] and not report["student_gates"]["priced_cost"]


async def test_structural_short_circuit_does_not_consult_expected_label(monkeypatch, tmp_path):
    exact = pair_cases(load_dataset(DATASET), "test")[0]
    monkeypatch.setattr(frugal, "pair_cases", lambda *_: [(*exact[:3], "distinct")])
    verifier = Verifier()
    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "independent.json",
        student(),
        verifier,
        Embeddings(),
        live=True,
        embedding_input_price=0,
    )
    assert verifier.calls == 0
    assert report["judgments"][0]["actual"] == "exact_duplicate"
    assert not report["judgments"][0]["assertions"]["correct"]["value"]
    assert not report["judgments"][0]["assertions"]["no_false_equivalence"]["value"]
    assert not report["passed"]


async def test_budgets_and_missing_live_providers_fail_before_inference(tmp_path):
    class NeverCalled(RulesInference):
        async def extract(self, segment):
            pytest.fail("Preflight must run before paid inference")

    with pytest.raises(ValueError, match="budget"):
        await frugal.qualify(DATASET, GATES, tmp_path / "budget.json", NeverCalled(), max_cases=1)
    with pytest.raises(ValueError, match="configured"):
        await frugal.qualify(DATASET, GATES, tmp_path / "live.json", NeverCalled(), live=True)


async def test_training_export_is_traceable_train_only_and_immutable(tmp_path):
    output = tmp_path / "training.jsonl"
    report = await export_training(DATASET, output)
    dataset = load_dataset(DATASET)
    train = extraction_cases(dataset, "train")
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == len(train)
    assert set(report["source_ids"]) == {s.source for s in dataset.selected("train")}
    assert report["sources"] and all(s["split"] == "train" for s in report["sources"])
    assert report["case_ids"] == [c.id for c in train]
    for row, case in zip(rows, train, strict=True):
        assert json.loads(row["messages"][1]["content"])["text"] == case.text
        extraction = Extraction.model_validate_json(row["messages"][2]["content"])
        assert extraction.candidates[0].excerpt in case.text
    with pytest.raises(ValueError, match="overwrite"):
        await export_training(DATASET, output)
    with pytest.raises(ValueError, match="budget"):
        await export_training(DATASET, tmp_path / "limited.jsonl", max_cases=1)


async def test_training_export_exclusive_creation_survives_precheck_race(monkeypatch, tmp_path):
    output = tmp_path / "training.jsonl"
    await export_training(DATASET, output)
    original = output.read_bytes()
    monkeypatch.setattr(Path, "exists", lambda _: False)
    with pytest.raises(ValueError, match="overwrite"):
        await export_training(DATASET, output)
    assert output.read_bytes() == original


async def test_configured_teacher_receives_training_inputs_only(tmp_path):
    seen = []

    class Teacher(RulesInference):
        capabilities = Capabilities(model_id="synthetic-teacher")

        async def extract(self, segment):
            seen.append(segment.text)
            return await super().extract(segment)

    report = await export_training(DATASET, tmp_path / "fresh.jsonl", Teacher())
    assert seen == [c.text for c in extraction_cases(load_dataset(DATASET), "train")]
    assert report["teacher"] == "synthetic-teacher" and report["generation"] == "configured_teacher"


async def test_one_provider_error_fails_otherwise_accurate_live_qualification(tmp_path):
    correct = student()

    class FailsOnce:
        capabilities = correct.capabilities
        prompt_version = correct.prompt_version
        failed = False

        async def extract(self, segment):
            if not self.failed:
                self.failed = True
                raise ValueError("Synthetic provider failure")
            return await correct.extract(segment)

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "error.json",
        FailsOnce(),
        Verifier(),
        Embeddings(),
        live=True,
        embedding_input_price=0,
    )
    assert report["recall"] > 0.9
    assert not report["safety_gates"]["no_provider_errors"]
    assert not report["usage_complete"] and report["estimated_cost"] is None
    assert not report["passed"] and not report["student_meets_provisional_gates"]


async def test_semantic_provider_failure_retains_errors_and_unknown_cost(tmp_path):
    class FailsOnce(Verifier):
        failed = False

        async def verify(self, old, new):
            if not self.failed:
                self.failed = True
                raise RuntimeError("private provider details")
            return await super().verify(old, new)

    report = await frugal.qualify(
        DATASET,
        GATES,
        tmp_path / "semantic-error.json",
        student(),
        FailsOnce(),
        Embeddings(),
        live=True,
        embedding_input_price=0,
    )
    failure = next(row for row in report["judgments"] if "error" in row)
    assert failure["error"] == "RuntimeError" and not failure["valid"]
    assert not failure["assertions"]["valid"]["value"]
    assert not report["safety_gates"]["no_provider_errors"]
    assert not report["usage_complete"] and report["estimated_cost"] is None
    assert "private provider details" not in json.dumps(report)


async def test_framework_evaluator_failure_cannot_produce_a_qualification(tmp_path, monkeypatch):
    def broken_evaluator(*_):
        raise RuntimeError("private evaluator details")

    class NeverEmbed(Embeddings):
        async def embed(self, texts):
            pytest.fail("A failed evaluator must stop before embedding")

    monkeypatch.setattr(frugal._SemanticEvaluator, "evaluate", broken_evaluator)
    output = tmp_path / "framework-error.json"
    with pytest.raises(RuntimeError, match="Pydantic Evals failed while running semantic-pairs"):
        await frugal.qualify(
            DATASET,
            GATES,
            output,
            student(),
            Verifier(),
            NeverEmbed(),
            live=True,
        )
    assert not output.exists()


async def test_cancellation_stops_sequential_qualification_without_extra_calls(tmp_path):
    started = asyncio.Event()
    waiting = asyncio.Event()
    calls = []

    class PendingStudent:
        capabilities = Capabilities(model_id="synthetic-student")
        prompt_version = "synthetic-tests-only"

        async def extract(self, segment):
            calls.append(segment.id)
            started.set()
            await waiting.wait()
            raise AssertionError("The provider should have been cancelled")

    output = tmp_path / "cancelled.json"
    task = asyncio.create_task(frugal.qualify(DATASET, GATES, output, PendingStudent()))
    try:
        await asyncio.wait_for(started.wait(), timeout=30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(calls) == 1
    assert not output.exists()


@pytest.mark.parametrize("failure", ["exception", "missing-vectors"])
async def test_embedding_failure_is_raised_without_duplicate_calls(failure):
    class BrokenEmbeddings(Embeddings):
        requests = 0

        async def embed(self, texts):
            self.requests += 1
            if failure == "exception":
                raise TimeoutError("offline embedding failure")
            return EmbeddingResult(vectors=(), calls=1, input_tokens=len(texts))

    embeddings = BrokenEmbeddings()
    error = TimeoutError if failure == "exception" else ValueError
    with pytest.raises(error):
        await frugal.retrieval(load_dataset(DATASET), "test", 3, embeddings)
    assert embeddings.requests == 1


async def test_retrieval_ranking_failure_keeps_original_error_and_stops_queries(monkeypatch):
    rankings = []

    def broken_nearest(*args):
        rankings.append(args)
        raise ValueError("invalid test vector")

    class CountedEmbeddings(Embeddings):
        requests = 0

        async def embed(self, texts):
            self.requests += 1
            return await super().embed(texts)

    monkeypatch.setattr(frugal, "nearest", broken_nearest)
    embeddings = CountedEmbeddings()
    with pytest.raises(ValueError, match="invalid test vector"):
        await frugal.retrieval(load_dataset(DATASET), "test", 3, embeddings)
    assert embeddings.requests == len(rankings) == 1


@pytest.mark.parametrize("right", [(), (1.0,), (0.0, 0.0), (float("nan"), 1.0)])
def test_invalid_embedding_dimensions_and_values_are_rejected(right):
    with pytest.raises(ValueError):
        cosine((1.0, 0.0), right)


def test_vector_retrieval_is_text_independent_bounded_and_stable():
    vectors = {"encrypt records": (1.0, 0.0), "cipher documents": (1.0, 0.0), "other": (0.0, 1.0)}
    assert nearest((1.0, 0.0), vectors, 1)[0][0] == "cipher documents"
    pair = vector_pairs(vectors, 1, 1)
    assert pair == [("cipher documents", "encrypt records", 1.0)]
    assert not vector_pairs(vectors, 0, 4)


@pytest.mark.parametrize(
    "tests_pass,evaluations_pass,ui_pass",
    [
        (False, True, None),
        (True, False, None),
        (True, True, None),
        (True, True, False),
        (True, True, True),
    ],
)
async def test_combined_suite_requires_code_and_evaluation_success(
    monkeypatch,
    tmp_path,
    tests_pass,
    evaluations_pass,
    ui_pass,
):
    calls = []

    async def code(*_):
        return {"passed": tests_pass}

    async def evaluate(*_, **kwargs):
        calls.append(True)
        return {"passed": evaluations_pass}

    async def browser(*_):
        return {"passed": ui_pass}

    async def skill_evaluation(*_, **kwargs):
        return {
            "passed": True,
            "skills": {name: {"passed": True} for name in ("labeling", "extraction")},
        }

    async def nist_evaluation(*_, **kwargs):
        return {"passed": True}

    monkeypatch.setattr(suite, "code_tests", code)
    monkeypatch.setattr(suite, "qualify", evaluate)
    monkeypatch.setattr(suite, "evaluate_skills", skill_evaluation)
    monkeypatch.setattr(suite, "evaluate_nist", nist_evaluation)
    monkeypatch.setattr(suite, "browser_tests", browser)
    report = await suite.run_suite(
        Settings(), DATASET, GATES, tmp_path, unit_only=True, ui=ui_pass is not None
    )
    assert report["passed"] == (tests_pass and evaluations_pass and ui_pass is not False)
    assert bool(calls) == (tests_pass and ui_pass is not False)
    assert (tmp_path / "suite.json").exists()


async def test_browser_suite_rejects_missing_reports_and_skipped_tests(monkeypatch, tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}")
    reports = frontend / "test-results"
    reports.mkdir()
    create_report = False
    skipped = 0

    async def process(*args, **kwargs):
        assert args[1:] == ("run", "verify")
        assert kwargs["cwd"] == frontend

        async def communicate():
            return b"synthetic browser checks", None

        if create_report:
            (reports / "results.json").write_text(
                json.dumps({"stats": {"expected": 2, "skipped": skipped, "unexpected": 0}})
            )
        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setattr(suite.shutil, "which", lambda _: "synthetic-npm")
    monkeypatch.setattr(suite.asyncio, "create_subprocess_exec", process)
    assert not (await suite.browser_tests(tmp_path, frontend))["passed"]
    create_report = True
    skipped = 1
    assert not (await suite.browser_tests(tmp_path, frontend))["passed"]
    skipped = 0
    assert (await suite.browser_tests(tmp_path, frontend))["passed"]


async def test_combined_suite_does_not_accept_missing_junit_or_skip_integrations(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("EKB_INFERENCE", "openai")
    monkeypatch.setenv("EKB_EMBEDDING_MODEL_ID", "synthetic-configured-embedding")
    monkeypatch.setenv("EKB_ARTIFACT_BACKEND", "azure")
    monkeypatch.setenv("EKB_MODEL_API_KEY", "synthetic-not-a-secret")

    async def process(*args, **kwargs):
        environment = kwargs["env"]
        assert environment["EKB_INFERENCE"] == "rules"
        assert environment["EKB_ARTIFACT_BACKEND"] == "filesystem"
        assert not environment["EKB_EMBEDDING_MODEL_ID"]
        assert not environment["EKB_MODEL_API_KEY"]

        async def communicate():
            return b"synthetic test process", None

        if create_report:
            (tmp_path / "tests.xml").write_text(
                '<testsuites><testsuite tests="2" failures="0" errors="0" skipped="1"/>'
                "</testsuites>"
            )
        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setattr(suite.asyncio, "create_subprocess_exec", process)
    (tmp_path / "tests.xml").write_text('<testsuite tests="999" skipped="0"/>')
    create_report = False
    assert not (await suite.code_tests(tmp_path, False))["passed"]
    create_report = True
    assert not (await suite.code_tests(tmp_path, False))["passed"]
    assert (await suite.code_tests(tmp_path, True))["passed"]


def test_cli_live_configuration_split_and_suite_failures_have_nonzero_exit(monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("EKB_INFERENCE", "rules")
    for args in (["eval", "qualify", "--live"], ["eval", "qualify", "--split", "invalid"]):
        result = runner.invoke(app, args)
        assert result.exit_code != 0
    monkeypatch.delenv("EKB_TEST_DATABASE_URL", raising=False)
    result = runner.invoke(app, ["test"])
    assert result.exit_code != 0 and "EKB_TEST_DATABASE_URL" in result.output


def test_corpus_verifier_uses_retained_originals_without_database_state(tmp_path, monkeypatch):
    raw = b"Source evidence is retained exactly.\n"
    key = content_hash(raw)
    target = tmp_path / key[:2] / key
    target.parent.mkdir()
    target.write_bytes(raw)
    labels = SimpleNamespace(
        sources=[SimpleNamespace(id="source", sha256=key)],
        seeds=[SimpleNamespace(id="seed", source="source", quote="retained exactly")],
    )

    class RetainedRuntime:
        artifacts = FilesystemArtifactStore(tmp_path)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr("ekb.interfaces.cli.evaluation.Runtime", RetainedRuntime)
    monkeypatch.setattr("ekb.interfaces.cli.evaluation.load_dataset", lambda _: labels)
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "verify-corpus"])
    assert result.exit_code == 0, result.output
    assert '"verified_quotes": 1' in result.output
    labels.seeds[0].quote = "Fabricated"
    assert runner.invoke(app, ["eval", "verify-corpus"]).exit_code != 0
    labels.seeds[0].quote = "retained exactly"
    target.write_bytes(b"Changed original")
    assert runner.invoke(app, ["eval", "verify-corpus"]).exit_code != 0
    target.unlink()
    assert runner.invoke(app, ["eval", "verify-corpus"]).exit_code != 0


def test_eval_list_and_history_show_available_and_never_run_skill_datasets(tmp_path):
    runner = CliRunner()
    listed = runner.invoke(app, ["eval", "list", "--history-dir", str(tmp_path)])
    assert listed.exit_code == 0
    assert "never_run" in listed.output
    assert "skills-v1.json" in listed.output and "documents-v1.json" in listed.output
    history = runner.invoke(
        app,
        ["eval", "history", "--history-dir", str(tmp_path), "--skill", "labeling"],
    )
    assert history.exit_code == 0 and history.output.strip() == "[]"


@pytest.mark.parametrize("command", ["list", "history"])
def test_eval_catalog_rejects_corrupt_history_instead_of_reporting_no_runs(tmp_path, command):
    (tmp_path / "skill-index-invalid.json").write_text('{"run_id": "incomplete"}')
    result = CliRunner().invoke(app, ["eval", command, "--history-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "Cannot read evaluation history index" in result.output
