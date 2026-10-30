import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import ekb.adapters.inference as inference_module
import ekb.evaluation.history as history_module
import ekb.evaluation.limits as limits_module
import ekb.evaluation.skills as skills_module
from ekb.adapters.inference import AgentFrameworkInference, OpenAIInference, RulesInference
from ekb.adapters.retry import retry
from ekb.config import Settings
from ekb.domain.models import (
    Candidate,
    Capabilities,
    ContentLabel,
    ContentLabeling,
    Extraction,
    InferenceResult,
    LabelingResult,
    Modality,
    Scope,
    SemanticType,
    Usage,
)
from ekb.evaluation.history import list_skill_catalog, persist_run, skill_history
from ekb.evaluation.limits import DeploymentRateLimiter
from ekb.evaluation.skills import (
    EvalDataset,
    SkillCase,
    SkillDataset,
    SkillExecution,
    SkillQualityEvaluator,
    compare_skill_concurrency,
    evaluate_skills,
    label_metrics,
)
from ekb.interfaces.cli import app

DATASET = Path("evals") / "skills-v1.json"
GATES = Path("evals") / "gates.json"


class FixtureSkills(RulesInference):
    capabilities = Capabilities(model_id="synthetic-deployment")

    def __init__(self, dataset: SkillDataset) -> None:
        self.cases = {case.segment().id: case for case in dataset.cases}
        self.label_calls: list[str] = []
        self.extract_calls: list[str] = []
        self.fail_once: set[str] = set()
        self.omit_labels: set[str] = set()
        self.clear_scope: set[str] = set()
        self.extractions: dict[str, Extraction] = {}
        self.active = 0
        self.max_active = 0
        self.limiter = None

    @contextmanager
    def limit_model_requests(self, limiter):
        assert self.limiter is None
        self.limiter = limiter
        try:
            yield
        finally:
            self.limiter = None

    async def label(self, segment):
        case = self.cases[segment.id]
        if self.limiter is not None:
            await self.limiter.reserve("label-deployment", 1)
        self.label_calls.append(case.id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.002)
            if case.id in self.fail_once:
                self.fail_once.remove(case.id)
                raise RuntimeError("sensitive exception marker")
            labeling = case.labeling
            if case.id in self.omit_labels:
                labeling = ContentLabeling()
            elif case.id in self.clear_scope:
                labeling = labeling.model_copy(
                    update={
                        "labels": [
                            label.model_copy(update={"scope": Scope()}) for label in labeling.labels
                        ]
                    }
                )
            return LabelingResult(
                labeling=labeling,
                usage=Usage(
                    input_tokens=10,
                    output_tokens=5,
                    model_calls=1,
                    cost=0.001,
                    retry_count=0,
                    throttles=0,
                    cached_input_tokens=0,
                ),
                model_id="label-deployment",
                provider_model_ids=("label-snapshot-v1",),
                prompt_version="label-v1",
            )
        finally:
            self.active -= 1

    async def extract_scoped(self, segment, labeling):
        case = self.cases[segment.id]
        if self.limiter is not None:
            await self.limiter.reserve("extract-deployment", 1)
        assert labeling == case.labeling
        self.extract_calls.append(case.id)
        return InferenceResult(
            extraction=self.extractions.get(case.id, case.expected),
            usage=Usage(
                input_tokens=10,
                output_tokens=5,
                model_calls=1,
                cost=0.001,
                retry_count=0,
                throttles=0,
                cached_input_tokens=0,
            ),
            model_id="extract-deployment",
            provider_model_ids=("extract-snapshot-v1",),
            prompt_version="extract-v1",
        )


def dataset() -> SkillDataset:
    return SkillDataset.model_validate_json(DATASET.read_bytes())


def single_test_case_dataset(tmp_path):
    original = dataset()
    cases = tuple(
        next(case for case in original.cases if case.split == split)
        for split in ("train", "validation", "test")
    )
    path = tmp_path / "single-case-dataset.json"
    path.write_text(
        original.model_copy(update={"cases": cases}).model_dump_json(), encoding="utf-8"
    )
    return path, cases[-1]


@asynccontextmanager
async def mock_transport_adapter(provider, settings, handle):
    if provider == "framework":
        pytest.importorskip("agent_framework_openai")
        transport = pytest.importorskip("httpx2")
        from openai import AsyncOpenAI

        async with transport.AsyncClient(
            transport=transport.MockTransport(lambda request: handle(transport, request))
        ) as client:
            sdk = AsyncOpenAI(
                api_key="synthetic",
                base_url="https://example.test/v1",
                http_client=client,
                max_retries=0,
            )
            adapter = AgentFrameworkInference(settings, sdk_client=sdk)
            try:
                yield adapter
            finally:
                await adapter.close()
                await sdk.close()
    else:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: handle(httpx, request))
        ) as client:
            adapter = OpenAIInference(settings, client=client)
            try:
                yield adapter
            finally:
                await adapter.close()


async def test_framework_scores_match_domain_scorer_for_errors_and_scope(tmp_path):
    cases = [case for case in dataset().cases if case.split == "test"]
    inference = FixtureSkills(dataset())
    inference.fail_once.add("source-boundary-protection")
    inference.omit_labels.add("test-conceptual")
    inference.clear_scope.add("test-mixed-scopes")
    report = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "latest.json",
        inference,
        skill="labeling",
        live=True,
        max_concurrency=3,
        history_dir=tmp_path / "history",
        run_metadata={"provider": "synthetic", "labeling_deployment": "label-deployment"},
    )
    run = report["skills"]["labeling"]
    rows = run["cases"]
    predictions = [
        ContentLabeling.model_validate(row["result"]["labeling"])
        if row["result"]["labeling"] is not None
        else ContentLabeling()
        for row in rows
    ]
    assert [row["id"] for row in rows] == [case.id for case in cases]
    assert run["metrics"] == label_metrics(cases, predictions)
    for case, prediction, row in zip(cases, predictions, rows, strict=True):
        expected = label_metrics([case], [prediction])
        assert {key: row["scores"][key]["value"] for key in ("tp", "fp", "fn")} == {
            key: expected[key] for key in ("tp", "fp", "fn")
        }
    assert not rows[0]["assertions"]["case_exact_match"]["value"]
    assert report["status"] == "partial" and not run["safety_gates"]["no_errors"]
    assert not run["performance"]["usage_complete"]
    assert run["performance"]["measured_model_requests"] is None
    assert run["performance"]["reported_model_requests"] > 0
    assert run["performance"]["retry_count"] is None
    assert run["performance"]["throttle_count"] is None
    assert run["usage"]["retry_count"] is None and run["usage"]["throttles"] is None
    assert (
        next(row for row in rows if row["result"]["error"] is None)["result"]["usage"][
            "retry_count"
        ]
        == 0
    )
    assert run["actual_model_ids"] == ["label-snapshot-v1"]
    assert 1 < inference.max_active <= 3
    assert "sensitive exception marker" not in json.dumps(report)
    persisted = await asyncio.to_thread(Path(report["history_path"]).read_text)
    assert json.loads(persisted) == report
    catalog = list_skill_catalog((DATASET,), tmp_path / "history")
    label_entry = next(item for item in catalog if item["skill"] == "labeling")
    assert label_entry["status"] == "partial"
    assert label_entry["latest_deployment"] == "label-deployment"
    assert label_entry["latest_actual_model_ids"] == ["label-snapshot-v1"]


@pytest.mark.parametrize("reported", ["unknown", "none", "one", "mixed"])
async def test_multicall_provider_reports_only_explicitly_observed_retries(tmp_path, reported):
    selected = [case for case in dataset().cases if case.split == "test"]

    class MultiCallSkills(FixtureSkills):
        max_requests_per_skill = 2

        async def label(self, segment):
            result = await super().label(segment)
            case = self.cases[segment.id]
            count = (
                None
                if reported == "unknown" or (reported == "mixed" and case.id == selected[0].id)
                else 0
                if reported == "none"
                else 1
            )
            return result.model_copy(
                update={
                    "usage": result.usage.model_copy(
                        update={"model_calls": 2, "unmetered_model_calls": 0, "retry_count": count}
                    )
                }
            )

    report = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "latest.json",
        MultiCallSkills(dataset()),
        skill="labeling",
        live=True,
        max_model_calls=2 * len(selected),
        history_dir=tmp_path / "history",
    )
    run = report["skills"]["labeling"]
    expected = (
        None if reported in {"unknown", "mixed"} else len(selected) if reported == "one" else 0
    )
    assert run["performance"]["measured_model_requests"] == 2 * len(selected)
    assert run["performance"]["retry_count"] == run["usage"]["retry_count"] == expected
    assert run["performance"]["throttle_count"] == 0
    assert run["performance"]["usage_complete"]
    if reported == "mixed":
        assert run["cases"][0]["result"]["usage"]["retry_count"] is None
        assert run["cases"][1]["result"]["usage"]["retry_count"] == 1


async def test_legacy_skill_result_and_history_remain_readable(tmp_path):
    legacy = {"requires_model": True, "usage": {"model_calls": 2, "input_tokens": 10}}
    result = SkillExecution.model_validate(legacy)
    assert result.usage.model_calls == 2 and result.usage.retry_count is None
    report = {
        "run_id": "legacy",
        "recorded_at": "2026-01-01T00:00:00Z",
        "status": "completed",
        "dataset_path": str(DATASET),
        "dataset_version": "v1",
        "dataset_sha256": "legacy",
        "split": "test",
        "model_id": "legacy-model",
        "live": True,
        "errors": 0,
        "passed": True,
        "skills": {
            "labeling": {
                "metrics": {},
                "quality_gates": {},
                "performance": {"retry_count": 1},
                "qualified": False,
                "errors": 0,
                "cases": [{"id": "legacy-case", "result": legacy}],
            }
        },
    }
    path = await persist_run(report, history=tmp_path)
    original = path.read_bytes()
    assert skill_history(history=tmp_path)[0]["performance"]["retry_count"] == 1
    assert path.read_bytes() == original


async def test_framework_submission_and_result_association_are_bounded(tmp_path, monkeypatch):
    original = dataset()
    template = next(case for case in original.cases if case.id == "test-conceptual")
    extra = tuple(
        template.model_copy(update={"id": f"batch-{index}", "family": f"family-{index}"})
        for index in range(25)
    )
    expanded = SkillDataset.model_validate(
        original.model_copy(update={"cases": original.cases + extra}).model_dump()
    )
    dataset_path = tmp_path / "cases.json"
    dataset_path.write_text(expanded.model_dump_json(), encoding="utf-8")
    submitted = []
    original_evaluate = EvalDataset.evaluate

    async def observe(self, *args, **kwargs):
        submitted.append(len(self.cases))
        return await original_evaluate(self, *args, **kwargs)

    monkeypatch.setattr(EvalDataset, "evaluate", observe)
    inference = FixtureSkills(expanded)
    report = await evaluate_skills(
        dataset_path,
        GATES,
        tmp_path / "latest.json",
        inference,
        skill="labeling",
        live=True,
        max_concurrency=3,
        history_dir=tmp_path / "history",
    )
    assert len(submitted) > 1 and max(submitted) <= 6
    assert 1 < inference.max_active <= 3
    assert [row["id"] for row in report["skills"]["labeling"]["cases"]] == [
        case.id for case in expanded.cases if case.split == "test"
    ]
    assert report["skills"]["labeling"]["metrics"]["recall"] == 1


async def test_false_mandate_still_fails_the_extraction_gate(tmp_path):
    original = dataset()
    text = "Teams should retain records, and systems must produce an audit trail."
    label = ContentLabel(semantic_type=SemanticType.TECHNICAL_REQUIREMENT, excerpt=text)
    expected = Candidate(
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
        statement="Teams should retain records",
        excerpt="Teams should retain records",
        modality=Modality.RECOMMENDED,
    )
    case = SkillCase(
        id="false-mandate",
        family="false-mandate-family",
        split="test",
        text=text,
        labeling=ContentLabeling(labels=[label]),
        expected=Extraction(candidates=[expected]),
    )
    expanded = SkillDataset.model_validate(
        original.model_copy(update={"cases": original.cases + (case,)}).model_dump()
    )
    dataset_path = tmp_path / "cases.json"
    dataset_path.write_text(expanded.model_dump_json(), encoding="utf-8")
    inference = FixtureSkills(expanded)
    inference.extractions[case.id] = Extraction(
        candidates=[
            Candidate(
                semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
                statement=text,
                excerpt=text,
                modality=Modality.MANDATED,
            )
        ]
    )
    report = await evaluate_skills(
        dataset_path,
        GATES,
        tmp_path / "latest.json",
        inference,
        skill="extraction",
        history_dir=tmp_path / "history",
    )
    run = report["skills"]["extraction"]
    assert run["errors"] == 0
    assert not run["safety_gates"]["no_false_mandates"] and not report["passed"]
    row = next(item for item in run["cases"] if item["id"] == case.id)
    assert {key: row["scores"][key]["value"] for key in ("tp", "fp", "fn")} == {
        "tp": 0,
        "fp": 1,
        "fn": 1,
    }


async def test_deployment_rate_limits_and_cancellation_are_independent(monkeypatch):
    clock = [0.0]

    async def advance(delay):
        clock[0] += delay

    monkeypatch.setattr(limits_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(limits_module, "sleep", advance)
    limiter = DeploymentRateLimiter(
        requests_per_minute=3, tokens_per_minute=150, estimated_tokens_per_request=50
    )
    await limiter.reserve("label", 2)
    await limiter.reserve("extract", 2)
    await limiter.reserve("label", 1)
    assert clock[0] == 0
    await limiter.reserve("label", 1)
    assert clock[0] == 60
    with pytest.raises(ValueError, match="reservation exceeds"):
        limiter.check_capacity(4)

    waiting = asyncio.Event()
    blocked = asyncio.Event()

    async def wait(_):
        waiting.set()
        await blocked.wait()

    monkeypatch.setattr(limits_module, "sleep", wait)
    limited = DeploymentRateLimiter(requests_per_minute=1)
    await limited.reserve("deployment", 1)
    task = asyncio.create_task(limited.reserve("deployment", 1))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await limited.reserve("other-deployment", 1)


async def test_rate_limit_requires_provider_control_and_resets_after_cancellation(
    tmp_path, monkeypatch
):
    class UncontrolledSkills(RulesInference):
        capabilities = Capabilities(model_id="synthetic")

    limiter = DeploymentRateLimiter(requests_per_minute=1)
    with pytest.raises(ValueError, match="per-request provider control"):
        await evaluate_skills(
            DATASET,
            GATES,
            tmp_path / "unsupported.json",
            UncontrolledSkills(),
            skill="labeling",
            live=True,
            history_dir=tmp_path / "history",
            rate_limiter=limiter,
        )

    waiting = asyncio.Event()
    blocked = asyncio.Event()

    async def wait(_):
        waiting.set()
        await blocked.wait()

    monkeypatch.setattr(limits_module, "sleep", wait)
    await limiter.reserve("label-deployment", 1)
    inference = FixtureSkills(dataset())
    task = asyncio.create_task(
        evaluate_skills(
            DATASET,
            GATES,
            tmp_path / "cancelled.json",
            inference,
            skill="labeling",
            live=True,
            history_dir=tmp_path / "history",
            rate_limiter=limiter,
        )
    )
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert inference.limiter is None and not inference.label_calls
    assert limiter.requests_reserved() == {"label-deployment": 1}
    assert json.loads((tmp_path / "cancelled.json").read_text())["status"] == "cancelled"


async def test_builtin_limiter_scope_resets_when_request_is_cancelled(tmp_path, monkeypatch):
    case = next(case for case in dataset().cases if case.split == "test")
    waiting, blocked = asyncio.Event(), asyncio.Event()

    async def wait(_):
        waiting.set()
        await blocked.wait()

    def handle(_request):
        return httpx.Response(
            200,
            json={
                "model": "snapshot",
                "choices": [{"message": {"content": case.labeling.model_dump_json()}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    monkeypatch.setattr(limits_module, "sleep", wait)
    limiter = DeploymentRateLimiter(requests_per_minute=1)
    await limiter.reserve("deployment", 1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = OpenAIInference(Settings(model_id="deployment", model_retries=0), client=client)
        task = asyncio.create_task(
            evaluate_skills(
                DATASET,
                GATES,
                tmp_path / "cancelled.json",
                adapter,
                skill="labeling",
                live=True,
                max_concurrency=1,
                max_model_calls=3,
                history_dir=tmp_path / "history",
                rate_limiter=limiter,
            )
        )
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = await asyncio.wait_for(adapter.label(case.segment()), timeout=2)
    assert result.provider_model_ids == ("snapshot",)
    assert limiter.requests_reserved() == {"deployment": 1}


async def test_both_skills_reserve_every_live_case_and_record_estimates(tmp_path):
    selected = [case for case in dataset().cases if case.split == "test"]

    class OrderedSkills(FixtureSkills):
        async def extract_scoped(self, segment, labeling):
            assert len(self.label_calls) == len(selected)
            return await super().extract_scoped(segment, labeling)

    inference = OrderedSkills(dataset())
    limiter = DeploymentRateLimiter(
        requests_per_minute=len(selected),
        tokens_per_minute=len(selected) * 100,
        estimated_tokens_per_request=100,
    )
    report = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "latest.json",
        inference,
        live=True,
        max_concurrency=3,
        max_model_calls=2 * len(selected),
        history_dir=tmp_path / "history",
        rate_limiter=limiter,
        run_metadata={
            "provider": "synthetic",
            "labeling_deployment": "label-deployment",
            "deployment": "extract-deployment",
        },
    )
    assert len(inference.label_calls) == len(inference.extract_calls) == len(selected)
    assert report["passed"]
    for name in ("labeling", "extraction"):
        performance = report["skills"][name]["performance"]
        assert performance["measured_model_requests"] == len(selected)
        assert performance["rate_limit_estimated_requests"] == len(selected)
        assert performance["rate_limit_estimated_tokens"] == len(selected) * 100
    assert report["skills"]["extraction"]["actual_model_ids"] == ["extract-snapshot-v1"]
    assert report["skills"]["labeling"]["rate_limited_requests_by_deployment"] == {
        "label-deployment": len(selected)
    }


async def test_provider_retries_stay_within_reservation_and_report_actual_models(
    tmp_path, monkeypatch
):
    case = next(case for case in dataset().cases if case.split == "test")
    responses = 0

    def handle(_request):
        nonlocal responses
        responses += 1
        if responses in {1, 2, 4, 5}:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "model": "snapshot-a" if responses == 3 else "snapshot-b",
                "choices": [
                    {
                        "message": {
                            "content": (
                                "not-json" if responses == 3 else case.labeling.model_dump_json()
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    async def fast_retry(operation):
        return await retry(operation, base_delay=0)

    monkeypatch.setattr(inference_module, "retry", fast_retry)
    settings = Settings(
        model_id="deployment", model_retries=1, model_input_price=1, model_output_price=1
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = OpenAIInference(settings, client=client)
        limiter = DeploymentRateLimiter(requests_per_minute=6)
        report = await evaluate_skills(
            DATASET,
            GATES,
            tmp_path / "latest.json",
            adapter,
            skill="labeling",
            live=True,
            max_concurrency=1,
            max_model_calls=6,
            history_dir=tmp_path / "history",
            rate_limiter=limiter,
        )
    run = report["skills"]["labeling"]
    assert responses == run["performance"]["measured_model_requests"] == 6
    assert run["performance"]["model_call_budget_reserved"] == 6
    assert run["performance"]["rate_limit_estimated_requests"] == 6
    assert run["performance"]["retry_count"] == 5
    assert run["cases"][0]["result"]["usage"]["retry_count"] == 5
    assert run["performance"]["throttle_count"] == 4
    assert run["performance"]["unmetered_model_requests"] == 4
    assert not run["performance"]["usage_complete"]
    assert run["performance"]["measured_input_tokens"] is None
    assert run["performance"]["measured_cost"] is None
    assert run["actual_model_ids"] == ["snapshot-a", "snapshot-b"]
    assert run["cases"][0]["result"]["provider_model_identity_complete"] is False
    assert report["status"] == "partial"


@pytest.mark.parametrize("provider", ["direct", "framework"])
async def test_validation_retry_with_missing_usage_is_not_priced(tmp_path, provider):
    dataset_path, case = single_test_case_dataset(tmp_path)
    responses = 0

    def handle(transport, _request):
        nonlocal responses
        responses += 1
        response = {
            "id": "offline-response",
            "object": "chat.completion",
            "created": 0,
            "model": "snapshot",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "not-json"
                        if responses == 1
                        else case.labeling.model_dump_json(),
                    },
                }
            ],
        }
        if responses > 1:
            response["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        return transport.Response(200, json=response)

    settings = Settings(
        model_id="deployment", model_retries=1, model_input_price=1, model_output_price=1
    )
    async with mock_transport_adapter(provider, settings, handle) as adapter:
        report = await evaluate_skills(
            dataset_path,
            GATES,
            tmp_path / "latest.json",
            adapter,
            skill="labeling",
            live=True,
            max_model_calls=6,
            history_dir=tmp_path / "history",
        )
    run = report["skills"]["labeling"]
    performance = run["performance"]
    assert responses == performance["measured_model_requests"] == 2
    assert performance["retry_count"] == 1 and performance["throttle_count"] == 0
    assert performance["unmetered_model_requests"] == 1
    assert performance["reported_input_tokens"] == 10 and performance["reported_output_tokens"] == 5
    assert performance["measured_input_tokens"] is performance["measured_cost"] is None
    assert not performance["usage_complete"] and not run["qualified"]
    assert run["usage"]["unmetered_model_calls"] == 1 and run["usage"]["cost"] is None


async def test_framework_counts_real_wrapped_transport_and_validation_retries(monkeypatch):
    case = next(case for case in dataset().cases if case.split == "test")
    responses = 0

    def handle(transport, request):
        nonlocal responses
        responses += 1
        if responses == 1:
            return transport.Response(429, json={"error": {"message": "offline throttle"}})
        if responses == 2:
            raise transport.ConnectError("offline connection failure", request=request)
        if responses == 4:
            return transport.Response(503, json={"error": {"message": "offline server failure"}})
        return transport.Response(
            200,
            json={
                "id": "offline-response",
                "object": "chat.completion",
                "created": 0,
                "model": "snapshot",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": (
                                "not-json" if responses == 3 else case.labeling.model_dump_json()
                            ),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    async def fast_retry(operation):
        return await retry(operation, base_delay=0)

    monkeypatch.setattr(inference_module, "retry", fast_retry)
    limiter = DeploymentRateLimiter(requests_per_minute=5)
    settings = Settings(
        model_id="deployment", model_retries=1, model_input_price=1, model_output_price=1
    )
    async with mock_transport_adapter("framework", settings, handle) as adapter:
        with adapter.limit_model_requests(limiter):
            result = await adapter.label(case.segment())
    assert responses == result.usage.model_calls == 5
    assert result.usage.retry_count == 4 and result.usage.throttles == 1
    assert result.usage.validation_failures == 1
    assert result.usage.unmetered_model_calls == 3 and result.usage.cost is None
    assert limiter.requests_reserved() == {"deployment": 5}
    assert result.provider_model_identity_complete is False


async def test_framework_does_not_retry_wrapped_nontransient_errors():
    case = next(case for case in dataset().cases if case.split == "test")
    responses = 0

    def handle(transport, _request):
        nonlocal responses
        responses += 1
        return transport.Response(401, json={"error": {"message": "offline authorization failure"}})

    async with mock_transport_adapter(
        "framework", Settings(model_id="deployment"), handle
    ) as adapter:
        with pytest.raises(httpx.HTTPStatusError) as raised:
            await adapter.label(case.segment())
    assert responses == 1 and raised.value.response.status_code == 401


async def test_failed_framework_retries_keep_totals_unknown(tmp_path, monkeypatch):
    dataset_path, _ = single_test_case_dataset(tmp_path)
    responses = 0

    def handle(transport, _request):
        nonlocal responses
        responses += 1
        return transport.Response(429, json={"error": {"message": "offline throttle"}})

    async def fast_retry(operation):
        return await retry(operation, base_delay=0)

    monkeypatch.setattr(inference_module, "retry", fast_retry)
    limiter = DeploymentRateLimiter(requests_per_minute=3)
    async with mock_transport_adapter(
        "framework", Settings(model_id="deployment", model_retries=0), handle
    ) as adapter:
        report = await evaluate_skills(
            dataset_path,
            GATES,
            tmp_path / "latest.json",
            adapter,
            skill="labeling",
            live=True,
            max_model_calls=3,
            history_dir=tmp_path / "history",
            rate_limiter=limiter,
        )
    performance = report["skills"]["labeling"]["performance"]
    usage = report["skills"]["labeling"]["usage"]
    assert report["status"] == "failed" and responses == 3
    assert performance["rate_limit_estimated_requests"] == 3
    assert performance["measured_model_requests"] is None
    assert performance["retry_count"] is performance["throttle_count"] is None
    assert usage["retry_count"] is usage["throttles"] is usage["unmetered_model_calls"] is None


async def test_framework_sdk_retries_cannot_bypass_adapter_request_bound():
    pytest.importorskip("agent_framework_openai")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key="synthetic", max_retries=2)
    try:
        with pytest.raises(ValueError, match="Disable SDK retries"):
            AgentFrameworkInference(Settings(model_id="synthetic"), sdk_client=client)
    finally:
        await client.close()


async def test_rate_limit_is_reapplied_to_retries_in_later_windows(tmp_path, monkeypatch):
    case = next(case for case in dataset().cases if case.split == "test")
    clock = [0.0]
    request_times = []

    async def advance(delay):
        clock[0] += delay

    async def fast_retry(operation):
        return await retry(operation, base_delay=0)

    def handle(_request):
        request_times.append(clock[0])
        if len(request_times) < 3:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "model": "snapshot",
                "choices": [{"message": {"content": case.labeling.model_dump_json()}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    monkeypatch.setattr(limits_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(limits_module, "sleep", advance)
    monkeypatch.setattr(inference_module, "retry", fast_retry)
    limiter = DeploymentRateLimiter(requests_per_minute=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = OpenAIInference(Settings(model_id="deployment", model_retries=0), client=client)
        report = await evaluate_skills(
            DATASET,
            GATES,
            tmp_path / "latest.json",
            adapter,
            skill="labeling",
            live=True,
            max_concurrency=1,
            max_model_calls=3,
            history_dir=tmp_path / "history",
            rate_limiter=limiter,
        )
    assert request_times == [0, 60, 120]
    assert report["skills"]["labeling"]["performance"]["rate_limit_estimated_requests"] == 3


async def test_provider_reports_actual_model_and_cached_input_tokens():
    case = next(case for case in dataset().cases if case.split == "test")

    def handle(_request):
        return httpx.Response(
            200,
            json={
                "model": "provider-snapshot-2026",
                "choices": [{"message": {"content": case.labeling.model_dump_json()}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 4},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await OpenAIInference(Settings(model_id="deployment"), client=client).label(
            case.segment()
        )
    assert result.model_id == "deployment"
    assert result.provider_model_ids == ("provider-snapshot-2026",)
    assert result.provider_model_identity_complete is True
    assert result.usage.cached_input_tokens == 4
    assert result.usage.unmetered_model_calls == result.usage.throttles == 0


async def test_provider_identity_is_incomplete_if_a_validation_response_omits_model():
    case = next(case for case in dataset().cases if case.split == "test")
    responses = 0

    def handle(_request):
        nonlocal responses
        responses += 1
        result = {
            "choices": [
                {
                    "message": {
                        "content": "not-json" if responses == 1 else case.labeling.model_dump_json()
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        if responses == 2:
            result["model"] = "snapshot"
        return httpx.Response(200, json=result)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await OpenAIInference(
            Settings(model_id="deployment", model_retries=1), client=client
        ).label(case.segment())
    assert result.provider_model_ids == ("snapshot",)
    assert result.provider_model_identity_complete is False


async def test_framework_evaluator_errors_are_failed_not_silently_qualified(tmp_path, monkeypatch):
    original = SkillQualityEvaluator.evaluate

    def broken(self, context):
        if context.inputs == "test-conceptual":
            raise RuntimeError("private evaluator failure")
        return original(self, context)

    monkeypatch.setattr(SkillQualityEvaluator, "evaluate", broken)
    report = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "latest.json",
        FixtureSkills(dataset()),
        skill="labeling",
        live=True,
        history_dir=tmp_path / "history",
    )
    assert report["status"] == "partial" and not report["passed"]
    assert report["failure_details"]["labeling:test-conceptual"] == "evaluation_case_failed"
    assert not report["skills"]["labeling"]["qualified"]
    assert "private evaluator failure" not in json.dumps(report)


async def test_report_index_failure_is_recoverable_and_history_is_immutable(tmp_path, monkeypatch):
    history = tmp_path / "history"
    report = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "latest.json",
        FixtureSkills(dataset()),
        skill="labeling",
        history_dir=history,
    )
    original_write = history_module._write_once

    def fail_index(path, value):
        if path.name.startswith("skill-index-"):
            raise OSError("index unavailable")
        original_write(path, value)

    monkeypatch.setattr(history_module, "_write_once", fail_index)
    with pytest.raises(OSError, match="index unavailable"):
        await persist_run({**report, "run_id": "unindexed"}, history=history)
    assert (history / "skill-eval-unindexed.json").is_file()
    rows = skill_history(skill="labeling", history=history)
    assert {row["run_id"] for row in rows} == {report["run_id"], "unindexed"}

    path = history / f"skill-eval-{report['run_id']}.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        await evaluate_skills(DATASET, GATES, path, FixtureSkills(dataset()), history_dir=history)
    assert path.read_bytes() == before

    results = await asyncio.gather(
        persist_run({"run_id": "race", "value": 1}, history=history, prefix="skill-comparison"),
        persist_run({"run_id": "race", "value": 2}, history=history, prefix="skill-comparison"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, FileExistsError) for result in results) == 1
    assert json.loads((history / "skill-comparison-race.json").read_text())["value"] in {1, 2}


async def test_failed_atomic_publication_leaves_no_partial_report(tmp_path, monkeypatch):
    def fail_link(*_):
        raise OSError("publication failed")

    monkeypatch.setattr(history_module.os, "link", fail_link)
    with pytest.raises(OSError, match="publication failed"):
        await persist_run({"run_id": "failed"}, history=tmp_path, prefix="skill-comparison")
    assert not list(tmp_path.iterdir())


def test_staging_name_collision_does_not_remove_another_writer(tmp_path, monkeypatch):
    class FixedUUID:
        hex = "f" * 32

    monkeypatch.setattr(history_module, "uuid4", lambda: FixedUUID())
    staging = tmp_path / f".pending-{FixedUUID.hex}"
    staging.write_text("another writer", encoding="utf-8")
    with pytest.raises(FileExistsError):
        history_module._write_once(tmp_path / "report.json", {"value": 1})
    assert staging.read_text(encoding="utf-8") == "another writer"


async def test_latest_output_hardlink_cannot_mutate_an_immutable_report(tmp_path):
    history = tmp_path / "history"
    first = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "first.json",
        FixtureSkills(dataset()),
        skill="labeling",
        history_dir=history,
    )
    retained = Path(first["history_path"])
    before = await asyncio.to_thread(retained.read_bytes)
    alias = tmp_path / "latest.json"
    await asyncio.to_thread(os.link, retained, alias)
    second = await evaluate_skills(
        DATASET, GATES, alias, FixtureSkills(dataset()), skill="labeling", history_dir=history
    )
    assert await asyncio.to_thread(retained.read_bytes) == before
    assert json.loads(alias.read_text())["run_id"] == second["run_id"]


async def test_preflight_cancellation_records_run_without_calling_model(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def delayed_revision():
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test did not release preflight")
        return {"revision": None, "dirty": None}

    monkeypatch.setattr(skills_module, "_git_revision", delayed_revision)
    inference = FixtureSkills(dataset())
    output = tmp_path / "latest.json"
    task = asyncio.create_task(
        evaluate_skills(
            DATASET,
            GATES,
            output,
            inference,
            skill="labeling",
            live=True,
            history_dir=tmp_path / "history",
        )
    )
    assert await asyncio.to_thread(entered.wait, 10)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    report = json.loads(output.read_text())
    assert report["status"] == "cancelled" and not inference.label_calls
    assert all(
        case["result"]["error"] == "cancelled_before_start"
        for case in report["skills"]["labeling"]["cases"]
    )


async def test_cancellation_during_report_write_waits_for_durable_history(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original_write = history_module._write_once

    def delayed_write(path, value):
        if path.name.startswith("skill-eval-"):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test did not release report write")
        original_write(path, value)

    monkeypatch.setattr(history_module, "_write_once", delayed_write)
    output = tmp_path / "latest.json"
    history = tmp_path / "history"
    task = asyncio.create_task(
        evaluate_skills(DATASET, GATES, output, FixtureSkills(dataset()), history_dir=history)
    )
    assert await asyncio.to_thread(entered.wait, 10)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    report = json.loads(output.read_text())
    persisted = await asyncio.to_thread(Path(report["history_path"]).read_text)
    assert json.loads(persisted) == report
    assert len(skill_history(history=history)) == 2


async def test_incompatible_comparisons_do_not_claim_speedup_or_start_under_budget(tmp_path):
    n = sum(case.split == "test" for case in dataset().cases)

    class ChangingSkills(FixtureSkills):
        async def label(self, segment):
            if len(self.label_calls) >= n:
                self.omit_labels.add("test-conceptual")
            return await super().label(segment)

    inference = ChangingSkills(dataset())
    kwargs = {
        "skill": "labeling",
        "split": "test",
        "max_cases": 100,
        "max_concurrency": 3,
        "calls_per_case": 1,
        "run_metadata": {"provider": "synthetic"},
        "history_dir": tmp_path / "history",
    }
    with pytest.raises(ValueError, match="equivalent work"):
        await compare_skill_concurrency(
            DATASET, GATES, tmp_path / "comparison.json", inference, max_model_calls=2, **kwargs
        )
    assert not inference.label_calls
    report = await compare_skill_concurrency(
        DATASET,
        GATES,
        tmp_path / "comparison.json",
        inference,
        max_model_calls=2 * n,
        **kwargs,
    )
    assert report["status"] == "incomparable" and not report["compatible"]
    assert "same_case_quality" in report["incompatibility_reasons"]
    assert report["aggregate"]["speedup"] is None
    assert len(skill_history(history=tmp_path / "history")) == 2


async def test_success_without_model_call_count_is_not_reported_as_measured(tmp_path):
    class UnknownRequests(FixtureSkills):
        async def label(self, segment):
            result = await super().label(segment)
            return result.model_copy(
                update={"usage": result.usage.model_copy(update={"model_calls": 0})}
            )

    report = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "latest.json",
        UnknownRequests(dataset()),
        skill="labeling",
        live=True,
        history_dir=tmp_path / "history",
    )
    run = report["skills"]["labeling"]
    assert not run["performance"]["requests_complete"]
    assert run["performance"]["measured_model_requests"] is None
    assert not run["performance"]["usage_complete"] and not run["qualified"]


async def test_configuration_fingerprint_includes_execution_limits(tmp_path):
    history = tmp_path / "history"
    first = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "one.json",
        RulesInference(),
        skill="labeling",
        max_concurrency=1,
        history_dir=history,
    )
    second = await evaluate_skills(
        DATASET,
        GATES,
        tmp_path / "two.json",
        RulesInference(),
        skill="labeling",
        max_concurrency=3,
        history_dir=history,
    )
    assert first["run_configuration"]["max_concurrency"] == 1
    assert second["run_configuration"]["max_concurrency"] == 3
    assert first["configuration_fingerprint"] != second["configuration_fingerprint"]


@pytest.mark.parametrize("identity", ["missing", "swapped"])
async def test_comparison_requires_matching_per_case_provider_identity(tmp_path, identity):
    selected = [case for case in dataset().cases if case.split == "test"]
    n = len(selected)
    selected_ids = [case.id for case in selected]

    class ChangingIdentity(FixtureSkills):
        async def label(self, segment):
            in_parallel_arm = len(self.label_calls) >= n
            result = await super().label(segment)
            case_id = self.cases[segment.id].id
            if identity == "missing":
                model_ids = ()
            elif case_id in selected_ids[:2]:
                index = selected_ids.index(case_id)
                model_ids = (f"snapshot-{1 - index if in_parallel_arm else index}",)
            else:
                model_ids = ("snapshot-other",)
            return result.model_copy(update={"provider_model_ids": model_ids})

    report = await compare_skill_concurrency(
        DATASET,
        GATES,
        tmp_path / "comparison.json",
        ChangingIdentity(dataset()),
        skill="labeling",
        split="test",
        max_cases=100,
        max_concurrency=3,
        max_model_calls=2 * n,
        calls_per_case=1,
        run_metadata={"provider": "synthetic"},
        history_dir=tmp_path / "history",
    )
    assert not report["compatible"] and report["aggregate"]["speedup"] is None
    expected = (
        "known_provider_model_identity" if identity == "missing" else "same_skill_and_evaluator"
    )
    assert expected in report["incompatibility_reasons"]


@pytest.mark.parametrize("cancel_at", ["between_arms", "bounded_parallel"])
async def test_cancelled_comparison_persists_linked_partial_history(
    tmp_path, monkeypatch, cancel_at
):
    n = sum(case.split == "test" for case in dataset().cases)
    entered, blocked = asyncio.Event(), asyncio.Event()
    cooldowns = 0

    class WaitingSkills(FixtureSkills):
        async def label(self, segment):
            if cancel_at == "bounded_parallel" and len(self.label_calls) >= n:
                entered.set()
                await blocked.wait()
            return await super().label(segment)

    async def cooldown():
        nonlocal cooldowns
        cooldowns += 1
        if cancel_at == "between_arms" and cooldowns == 2:
            entered.set()
            await blocked.wait()

    limiter = DeploymentRateLimiter(requests_per_minute=n + 1)
    monkeypatch.setattr(limiter, "wait_until_clear", cooldown)
    history = tmp_path / "history"
    output = tmp_path / "comparison.json"
    task = asyncio.create_task(
        compare_skill_concurrency(
            DATASET,
            GATES,
            output,
            WaitingSkills(dataset()),
            skill="labeling",
            split="test",
            max_cases=100,
            max_concurrency=3,
            max_model_calls=2 * n,
            calls_per_case=1,
            run_metadata={"provider": "synthetic", "labeling_deployment": "label-deployment"},
            history_dir=history,
            rate_limiter=limiter,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    report = json.loads(output.read_text())
    assert report["status"] == "cancelled" and report["active_arm"] == cancel_at
    persisted = await asyncio.to_thread(Path(report["history_path"]).read_text)
    assert json.loads(persisted) == report
    assert (history / f"skill-eval-{report['sequential_run_id']}.json").is_file()
    if cancel_at == "bounded_parallel":
        child = history / f"skill-eval-{report['parallel_run_id']}.json"
        assert json.loads(child.read_text())["status"] == "cancelled"
    else:
        assert report["parallel_run_id"] is None


async def test_comparison_shares_quota_and_cools_down_between_arms(tmp_path, monkeypatch):
    n = sum(case.split == "test" for case in dataset().cases)
    clock, delays = [0.0], []

    async def advance(delay):
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(limits_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(limits_module, "sleep", advance)
    limiter = DeploymentRateLimiter(requests_per_minute=n)
    report = await compare_skill_concurrency(
        DATASET,
        GATES,
        tmp_path / "comparison.json",
        FixtureSkills(dataset()),
        skill="labeling",
        split="test",
        max_cases=100,
        max_concurrency=3,
        max_model_calls=2 * n,
        calls_per_case=1,
        run_metadata={"provider": "synthetic", "labeling_deployment": "label-deployment"},
        history_dir=tmp_path / "history",
        rate_limiter=limiter,
    )
    assert report["compatible"]
    assert delays == [60]
    assert limiter.requests_reserved() == {"label-deployment": 2 * n}
    for arm in ("sequential", "parallel"):
        performance = report["quality_parity"]["labeling"][f"{arm}_performance"]
        assert performance["rate_limit_estimated_requests"] == n


async def test_failed_comparison_arm_preserves_partial_usage_as_unknown(tmp_path):
    n = sum(case.split == "test" for case in dataset().cases)
    inference = FixtureSkills(dataset())
    inference.fail_once.add("test-conceptual")
    report = await compare_skill_concurrency(
        DATASET,
        GATES,
        tmp_path / "comparison.json",
        inference,
        skill="labeling",
        split="test",
        max_cases=100,
        max_concurrency=3,
        max_model_calls=2 * n,
        calls_per_case=1,
        run_metadata={"provider": "synthetic"},
        history_dir=tmp_path / "history",
    )
    assert report["status"] == "incomparable"
    assert report["sequential_status"] == "partial"
    assert "complete_arms" in report["incompatibility_reasons"]
    assert report["aggregate"]["sequential_failures"] == 1
    assert report["aggregate"]["sequential_model_requests"] is None
    assert report["aggregate"]["combined_measured_cost"] is None
    assert report["aggregate"]["speedup"] is None


@pytest.mark.parametrize("cache_tokens", [None, 5])
async def test_comparison_rejects_unknown_or_different_provider_cache(tmp_path, cache_tokens):
    n = sum(case.split == "test" for case in dataset().cases)

    class ChangingCache(FixtureSkills):
        async def label(self, segment):
            in_parallel_arm = len(self.label_calls) >= n
            result = await super().label(segment)
            if in_parallel_arm:
                return result.model_copy(
                    update={
                        "usage": result.usage.model_copy(
                            update={"cached_input_tokens": cache_tokens}
                        )
                    }
                )
            return result

    report = await compare_skill_concurrency(
        DATASET,
        GATES,
        tmp_path / "comparison.json",
        ChangingCache(dataset()),
        skill="labeling",
        split="test",
        max_cases=100,
        max_concurrency=3,
        max_model_calls=2 * n,
        calls_per_case=1,
        run_metadata={"provider": "synthetic"},
        history_dir=tmp_path / "history",
    )
    assert not report["compatible"]
    assert "same_measured_provider_cache" in report["incompatibility_reasons"]
    assert report["aggregate"]["speedup"] is None


def test_cli_lists_failed_and_never_run_skills_with_latest_history(tmp_path):
    inference = FixtureSkills(dataset())
    inference.fail_once = {case.id for case in dataset().cases if case.split == "test"}
    history = tmp_path / "history"
    report = asyncio.run(
        evaluate_skills(
            DATASET,
            GATES,
            tmp_path / "latest.json",
            inference,
            skill="labeling",
            live=True,
            run_metadata={"provider": "synthetic", "labeling_deployment": "label-deployment"},
            history_dir=history,
        )
    )
    assert report["status"] == "failed"
    runner = CliRunner()
    listed = runner.invoke(app, ["eval", "list", "--history-dir", str(history)])
    assert listed.exit_code == 0
    entries = json.loads(listed.output)
    labeling = next(
        item for item in entries if item["dataset"] == str(DATASET) and item["skill"] == "labeling"
    )
    extraction = next(
        item
        for item in entries
        if item["dataset"] == str(DATASET) and item["skill"] == "extraction"
    )
    assert labeling["status"] == "failed" and labeling["latest_metrics"] is not None
    assert labeling["latest_performance"]["measured_model_requests"] is None
    assert labeling["latest_provider"] == "synthetic"
    assert extraction["status"] == "never_run"
    history_result = runner.invoke(
        app, ["eval", "history", "--history-dir", str(history), "--skill", "labeling"]
    )
    assert history_result.exit_code == 0
    rows = json.loads(history_result.output)
    assert len(rows) == 1 and rows[0]["run_id"] == report["run_id"]
    assert rows[0]["status"] == "failed" and rows[0]["evaluator_fingerprint"]

    unauthorized = runner.invoke(app, ["eval", "skills", "--compare-concurrency"])
    assert unauthorized.exit_code != 0
    assert "--compare-concurrency requires explicit --live" in unauthorized.output
