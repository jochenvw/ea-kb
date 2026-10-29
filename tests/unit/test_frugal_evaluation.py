import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from ekb.adapters.inference import RulesInference
from ekb.config import Settings
from ekb.domain.models import (
    Candidate,
    Capabilities,
    Extraction,
    InferenceResult,
    Scope,
    Usage,
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
        assert variants["objective"].expected[0].kind == "objective"
        assert variants["injection"].expected[0].excerpt == seed.quote
        assert "approved enterprise policy" in variants["injection"].text
        assert variants["injection"].expected[0].kind == seed.kind
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
    assert objective.kind == "objective"
    assert objective.modality == "unspecified"
    assert regulation.kind == "requirement"
    assert regulation.modality == "mandated"
    assert cases["nist-csf-mission/source"].expected[0].kind == "requirement"
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
                    kind=e.kind,
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
            if a.control.evidence.excerpt == old.control.evidence.excerpt
            and b.control.evidence.excerpt == new.control.evidence.excerpt
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
                old_excerpt=old.control.evidence.excerpt,
                new_excerpt=new.control.evidence.excerpt,
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
                vectors=tuple((1.0, 0.0) for _ in texts), calls=1, input_tokens=len(texts)
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
    assert not report["passed"] and not report["student_meets_provisional_gates"]


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

    monkeypatch.setattr(suite, "code_tests", code)
    monkeypatch.setattr(suite, "qualify", evaluate)
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
