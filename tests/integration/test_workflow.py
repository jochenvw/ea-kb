import asyncio
from datetime import date
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from textual.widgets import Input, Static

from ekb.config import Settings
from ekb.domain.execution import CheckOptions, PipelineConfiguration, StageOptions
from ekb.domain.models import ConceptVersion, Scope, SemanticVersion
from ekb.domain.resolution import Decision
from ekb.interfaces.api import create_app
from ekb.interfaces.tui import KnowledgeApp
from ekb.ports.workflow import CheckResult
from ekb.runtime import Runtime
from ekb.services.knowledge import KnowledgeService
from ekb.services.reconciliation import BenchmarkService
from ekb.stages.enrichment import Check

pytestmark = pytest.mark.integration


async def prepare(database, tmp_path):
    directory = tmp_path / "sources"
    directory.mkdir()
    (directory / "one.md").write_text(
        "# Synthetic enterprise standard\n\nOperators must protect public web endpoints with WAF."
    )
    (directory / "two.md").write_text(
        "# Synthetic enterprise guideline\n\n"
        "Operators must protect external web endpoints with WAF."
    )
    runtime = Runtime(Settings(data_dir=tmp_path / "runtime"))
    await runtime.database.close()
    runtime.database = database
    runtime.knowledge = KnowledgeService(database)
    runtime.comparisons = BenchmarkService(database)
    config = PipelineConfiguration(stages={"indexing": StageOptions(directory=str(directory))})
    return runtime, directory, config


async def test_stage_order_check_caches_partial_execution_and_rejected_proposals(
    database, tmp_path
):
    runtime, directory, config = await prepare(database, tmp_path)
    try:
        first = await runtime.orchestrator().run(configuration=config)
        stages = sorted(
            [e for e in first.executions if e.kind == "stage"], key=lambda e: e.started_at
        )
        assert [e.processor_id for e in stages] == ["indexing", "enrichment", "issues"]
        assert first.run.status == "done"
        findings = await runtime.reconciliation.findings()
        duplicate = next(f for f in findings if f.action == "alias")
        before = await runtime.resolver.resolve_concept(duplicate.concept_ids[0])
        assert before.status == "not_established" and not before.versions
        decision = await runtime.reconciliation.decide(
            Decision(
                finding_id=duplicate.id,
                outcome="rejected",
                actor="test-reviewer",
                rationale="Distinct obligations in this synthetic example.",
            )
        )
        second = await runtime.orchestrator().run(configuration=config)
        checks = [e for e in second.executions if e.kind == "check"]
        assert all(
            e.status == "skipped" for e in checks if e.processor_id.startswith("enrichment/")
        )
        assert (await runtime.reconciliation.history(duplicate.id)) == [decision]
        assert not [
            i for i in await runtime.issues.list() if duplicate.id in i.finding_ids and i.observed
        ]
        partial = await runtime.orchestrator().run(("issues",), config)
        assert partial.run.status == "done"
        (directory / "new.md").write_text("Users must encrypt public endpoints.")
        await runtime.pipeline().scan(runtime.connectors(directory))
        with pytest.raises(ExceptionGroup):
            await runtime.orchestrator().run(("issues",), config)
    finally:
        await runtime.__aexit__()


async def test_backlog_tracks_latest_decisions_and_graph_preserves_proposal_direction(
    database, tmp_path
):
    from ekb.domain.execution import ArtifactReference, Execution
    from ekb.domain.models import now

    runtime, _, config = await prepare(database, tmp_path)
    try:
        await runtime.orchestrator().run(configuration=config)
        duplicate = next(f for f in await runtime.reconciliation.findings() if f.action == "alias")
        app = create_app(runtime)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                pending = (await client.get("/api/reconciliation/backlog?limit=1")).json()
                assert pending["total"] >= 1
                assert len(pending["items"]) == 1
                before = (await client.get("/api/reconciliation/backlog")).json()
                assert str(duplicate.id) in {item["finding"]["id"] for item in before["items"]}
                graph = (await client.get(f"/api/concepts/{duplicate.concept_ids[1]}/graph")).json()
                edge = next(e for e in graph["edges"] if e["status"] == "proposal")
                assert edge["source"] == str(duplicate.concept_ids[0])
                assert edge["target"] == str(duplicate.concept_ids[1])
                assert any(node["kind"] == "source" for node in graph["nodes"])
                assert any(e["status"] == "provenance" for e in graph["edges"])
                deferred = await runtime.reconciliation.decide(
                    Decision(
                        finding_id=duplicate.id,
                        outcome="deferred",
                        actor="reviewer",
                        rationale="More evidence required.",
                    )
                )
                queue = (await client.get("/api/reconciliation/backlog?status=deferred")).json()
                assert queue["items"][0]["decision"]["id"] == str(deferred.id)
                accepted = await runtime.reconciliation.decide(
                    Decision(
                        finding_id=duplicate.id,
                        outcome="accepted",
                        previous_id=deferred.id,
                        actor="reviewer",
                        rationale="Evidence reviewed.",
                    )
                )
                queue = (await client.get("/api/reconciliation/backlog")).json()
                assert str(duplicate.id) not in {item["finding"]["id"] for item in queue["items"]}
                await runtime.reconciliation.decide(
                    Decision(
                        finding_id=duplicate.id,
                        outcome="reversed",
                        previous_id=accepted.id,
                        actor="reviewer",
                        rationale="Reopen the interpretation.",
                    )
                )
                reversed_queue = (
                    await client.get("/api/reconciliation/backlog?status=reversed")
                ).json()
                assert reversed_queue["items"][0]["finding"]["id"] == str(duplicate.id)
                async with database() as uow:
                    await uow.workflow.save_execution(
                        Execution(
                            kind="stage",
                            processor_id="enrichment",
                            version="test",
                            fingerprint="test",
                            status="done",
                            finished_at=now(),
                            output=ArtifactReference(
                                contract="findings/v1",
                                revision="test",
                                producer_fingerprint="test",
                                source_revision="test",
                            ),
                        )
                    )
                    await uow.commit()
                assert (await client.get("/api/reconciliation/backlog")).json()["total"] == 0
                manual = duplicate.model_copy(update={"id": uuid4(), "check_id": "manual"})
                await runtime.reconciliation.propose(manual)
                queue = (await client.get("/api/reconciliation/backlog")).json()
                assert queue["total"] == 1 and queue["items"][0]["finding"]["id"] == str(manual.id)
                for _ in range(2):
                    await runtime.reconciliation.propose(manual.model_copy(update={"id": uuid4()}))
                await runtime.reconciliation.decide(
                    Decision(
                        finding_id=manual.id,
                        outcome="rejected",
                        actor="reviewer",
                        rationale="This manual proposal is not supported.",
                    )
                )
                first = (await client.get("/api/reconciliation/backlog?limit=1")).json()
                second = (
                    await client.get(
                        f"/api/reconciliation/backlog?limit=1&after={first['next_cursor']}"
                    )
                ).json()
                assert first["total"] == second["total"] == 2
                assert first["items"][0]["finding"]["id"] != second["items"][0]["finding"]["id"]
                assert second["next_cursor"] is None
                assert (
                    await client.get("/api/reconciliation/backlog?status=rejected")
                ).status_code == 422
                assert (await client.get(f"/api/concepts/{uuid4()}/graph")).status_code == 404
    finally:
        await runtime.__aexit__()


async def test_accepted_alias_canonical_resolution_reversal_and_restart(database, tmp_path):
    runtime, _, config = await prepare(database, tmp_path)
    try:
        await runtime.orchestrator().run(configuration=config)
        duplicate = next(f for f in await runtime.reconciliation.findings() if f.action == "alias")
        accepted = await runtime.reconciliation.decide(
            Decision(
                finding_id=duplicate.id,
                outcome="accepted",
                actor="test-reviewer",
                rationale="Reviewed synthetic source equivalence.",
            )
        )
        assert await runtime.reconciliation.decide(accepted) == accepted
        source, target = duplicate.concept_ids
        canonical = await runtime.knowledge.get(target)
        assert canonical is not None
        scope = Scope(organization="Example", platform="Azure")
        proposal = await runtime.reconciliation.propose_canonical(
            target, canonical.versions[0].id, scope
        )
        await runtime.reconciliation.decide(
            Decision(
                finding_id=proposal.id,
                outcome="accepted",
                actor="test-reviewer",
                rationale="Approved only for the synthetic Example/Azure scope.",
            )
        )
        await runtime.orchestrator().run(configuration=config)
        from ekb.services.knowledge_resolver import KnowledgeResolver

        restarted = KnowledgeResolver(database)
        resolved = await restarted.resolve_concept(source, scope)
        assert resolved.status == "resolved" and resolved.authoritative
        assert resolved.resolved_id == target and len(resolved.versions) == 1
        assert len(resolved.provenance) == 2 and resolved.decisions
        assert (await restarted.resolve_concept(source)).status == "not_established"
        source_concept = await runtime.knowledge.get(source)
        assert source_concept
        competing = await runtime.reconciliation.propose_canonical(
            source, source_concept.versions[0].id, scope
        )
        competing_decision = await runtime.reconciliation.decide(
            Decision(
                finding_id=competing.id,
                outcome="accepted",
                actor="test-reviewer",
                rationale="Deliberately ambiguous synthetic canonical selection.",
            )
        )
        ambiguous = await restarted.resolve_concept(source, scope)
        assert ambiguous.status == "ambiguous" and not ambiguous.authoritative
        await runtime.orchestrator().run(configuration=config)
        assert any(
            i.issue_type == "competing_canonical_sources" for i in await runtime.issues.list()
        )
        await runtime.reconciliation.decide(
            Decision(
                finding_id=competing.id,
                outcome="reversed",
                actor="test-reviewer",
                rationale="Restore the unique synthetic selection.",
                previous_id=competing_decision.id,
            )
        )
        reverse = duplicate.model_copy(
            update={
                "id": uuid4(),
                "concept_ids": (target, source),
                "check_id": "manual",
            }
        )
        await runtime.reconciliation.propose(reverse)
        with pytest.raises(ValueError, match="Cyclic"):
            await runtime.reconciliation.decide(
                Decision(
                    finding_id=reverse.id,
                    outcome="accepted",
                    actor="test-reviewer",
                    rationale="Deliberately invalid alias cycle.",
                )
            )
        await runtime.reconciliation.decide(
            Decision(
                finding_id=duplicate.id,
                outcome="reversed",
                actor="test-reviewer",
                rationale="Reverse the synthetic equivalence.",
                previous_id=accepted.id,
            )
        )
        assert (await restarted.resolve_concept(source, scope)).status == "not_established"
        assert len(await runtime.reconciliation.history(duplicate.id)) == 2
        assert await runtime.knowledge.provenance(source)
    finally:
        await runtime.__aexit__()


async def test_version_history_time_scopes_and_draft_safety(database, tmp_path):
    runtime, directory, _ = await prepare(database, tmp_path)
    try:
        await runtime.pipeline().scan(runtime.connectors(directory))
        concept = (await runtime.knowledge.search("public web"))[0]
        old = concept.versions[0]
        first = await runtime.reconciliation.propose_canonical(concept.id, old.id, Scope())
        await runtime.reconciliation.decide(
            Decision(
                finding_id=first.id,
                outcome="accepted",
                actor="operator",
                rationale="Historical baseline.",
                effective_at=date(2020, 1, 1),
            )
        )
        new = ConceptVersion(
            id=uuid4(),
            concept_id=concept.id,
            semantic_version=SemanticVersion.parse("1.0.0"),
            predecessor_id=old.id,
            candidate=old.candidate,
        )
        async with database() as uow:
            await uow.knowledge.add_version(new)
            await uow.commit()
        successor = await runtime.reconciliation.propose_canonical(concept.id, new.id, Scope())
        await runtime.reconciliation.decide(
            Decision(
                finding_id=successor.id,
                outcome="accepted",
                actor="operator",
                rationale="Stable successor.",
                effective_at=date(2025, 1, 1),
            )
        )
        historical = await runtime.resolver.resolve_concept(
            concept.id, profile="historical", as_of=date(2024, 1, 1)
        )
        assert historical.versions[0].id == old.id
        assert (await runtime.resolver.resolve_concept(concept.id)).versions[0].id == new.id
        assert (
            len((await runtime.resolver.resolve_concept(concept.id, profile="all")).versions) == 2
        )
        draft = ConceptVersion(
            id=uuid4(),
            concept_id=concept.id,
            semantic_version=SemanticVersion.parse("2.0.0-draft.1"),
            predecessor_id=new.id,
            candidate=old.candidate,
        )
        async with database() as uow:
            await uow.knowledge.add_version(draft)
            await uow.commit()
        unsafe = await runtime.reconciliation.propose_canonical(concept.id, draft.id, Scope())
        with pytest.raises(ValueError, match="limited-scope"):
            await runtime.reconciliation.decide(
                Decision(
                    finding_id=unsafe.id,
                    outcome="accepted",
                    actor="operator",
                    rationale="Unsafe draft.",
                )
            )
        safe = await runtime.reconciliation.propose_canonical(
            concept.id, draft.id, Scope(application="pilot"), allow_prerelease=True
        )
        await runtime.reconciliation.decide(
            Decision(
                finding_id=safe.id,
                outcome="accepted",
                actor="operator",
                rationale="Explicit pilot only.",
            )
        )
        assert (await runtime.resolver.resolve_concept(concept.id)).versions[0].id == new.id
        assert (
            await runtime.resolver.resolve_concept(concept.id, Scope(application="pilot"))
        ).versions[0].id == draft.id
        unsupported = draft.model_copy(
            update={
                "id": uuid4(),
                "semantic_version": SemanticVersion.parse("3.0.0"),
                "predecessor_id": draft.id,
                "candidate": old.candidate.model_copy(
                    update={
                        "statement": "Operators must deploy a fabricated product without evidence.",
                    }
                ),
            }
        )
        async with database() as uow:
            await uow.knowledge.add_version(unsupported)
            await uow.commit()
        with pytest.raises(ValueError, match="source evidence"):
            await runtime.reconciliation.propose_canonical(concept.id, unsupported.id, Scope())
    finally:
        await runtime.__aexit__()


async def test_plugin_changed_check_targeted_resume_and_execution_lease(database, tmp_path):
    runtime, _, config = await prepare(database, tmp_path)
    try:
        await runtime.orchestrator().run(configuration=config)
        orchestrator = runtime.orchestrator()
        enrichment = orchestrator.registry.stages["enrichment"]
        calls = 0

        async def flaky(context):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("Injected transient failure")
            return CheckResult()

        enrichment.checks = (*enrichment.checks, Check("plugin", flaky))
        no_retry = config.model_copy(update={"retries": 0})
        with pytest.raises(ExceptionGroup):
            await orchestrator.run(("enrichment",), no_retry)
        failed = await orchestrator.status()
        assert failed and failed.run.status == "failed"
        resumed = await orchestrator.run(resume=failed.run.id)
        assert resumed.run.status == "done" and calls == 2
        assert all(
            e.status == "skipped"
            for e in resumed.executions
            if e.kind == "check" and e.processor_id != "enrichment/plugin"
        )
        async with database() as uow:
            assert await uow.workflow.lease("knowledge-pipeline", uuid4())
            await uow.commit()
        with pytest.raises(ValueError, match="lease"):
            await orchestrator.run(("enrichment",), config)
    finally:
        await runtime.__aexit__()


async def test_issue_dismissal_observation_and_material_reopening(database, tmp_path):
    from ekb.domain.issues import Issue

    runtime, _, _ = await prepare(database, tmp_path)
    try:
        issue = Issue(
            id=uuid4(),
            issue_type="synthetic",
            severity="medium",
            confidence=0.9,
            concept_ids=(),
            check_id="fixture",
            check_version="1",
            evidence_revision="evidence-a",
            explanation="Synthetic condition.",
        )

        async def observe(records):
            async with database() as uow:
                await uow.issues.observe(records, "fixture", uuid4())
                await uow.commit()

        await observe((issue,))
        await runtime.issues.review(
            issue.id, "dismissed", "operator", "Known synthetic false positive"
        )
        await observe((issue,))
        same = await runtime.issues.get(issue.id)
        assert same and same.issue.status == "dismissed"
        await observe((issue.model_copy(update={"evidence_revision": "evidence-b"}),))
        changed = await runtime.issues.get(issue.id)
        assert changed and changed.issue.status == "reopened"
        await observe(())
        cleared = await runtime.issues.get(issue.id)
        assert cleared and not cleared.issue.observed and cleared.issue.status == "reopened"
        assert len(cleared.events) == 5
    finally:
        await runtime.__aexit__()


async def test_api_authorization_shared_reviews_resolver_and_tui(database, tmp_path):
    runtime, _, config = await prepare(database, tmp_path)
    try:
        await runtime.orchestrator().run(configuration=config)
        proposal = next(f for f in await runtime.reconciliation.findings() if f.action == "alias")
        app = create_app(runtime)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                assert (await client.get("/review")).status_code == 200
                assert (await client.get("/api/issues")).json()
                resolved = (
                    await client.get(f"/api/knowledge/{proposal.concept_ids[0]}/resolve")
                ).json()
                assert resolved["status"] == "not_established"
                data = Decision(
                    finding_id=proposal.id,
                    outcome="rejected",
                    actor="untrusted-body",
                    rationale="Shared review service.",
                ).model_dump(mode="json")
                assert (
                    await client.post("/api/reconciliation/decisions", json=data)
                ).status_code == 403
                runtime.settings.governance_token = SecretStr("synthetic-test-token")
                assert (
                    await client.post("/api/reconciliation/decisions", json=data)
                ).status_code == 401
                response = await client.post(
                    "/api/reconciliation/decisions",
                    json=data,
                    auth=("operator", "synthetic-test-token"),
                )
                assert response.status_code == 200 and response.json()["actor"] == "operator"
                denied = await client.post(
                    "/api/reconciliation/decisions",
                    json=data,
                    auth=("operator", "synthetic-test-token"),
                    headers={"Origin": "https://other.example"},
                )
                assert denied.status_code == 403
        tui = KnowledgeApp(runtime)
        async with tui.run_test(size=(160, 65)) as pilot:
            await pilot.pause()
            tui.query_one("#resolve-id", Input).value = str(proposal.concept_ids[0])
            worker = tui.action_review("resolve")
            await asyncio.wait_for(worker.wait(), timeout=10)
            assert "not_established" in str(tui.query_one("#evidence", Static).content)
    finally:
        await runtime.__aexit__()


@pytest.mark.parametrize("embedding_only", [False, True])
async def test_optional_semantic_provider_uses_durable_cache_and_preserves_proposals(
    database,
    tmp_path,
    embedding_only,
):
    from ekb.domain.models import Usage
    from ekb.domain.reconciliation import EmbeddingResult, SemanticJudgment, Verification

    class Embeddings:
        fingerprint = "synthetic-embeddings-v1"
        model_id = "synthetic-test-embeddings"
        calls = 0

        async def embed(self, texts):
            self.calls += 1
            return EmbeddingResult(
                vectors=tuple((1.0, 0.0) for _ in texts), calls=1, input_tokens=8
            )

    class Verifier:
        fingerprint = "synthetic-verifier-v1"
        calls = 0

        async def verify(self, old, new):
            self.calls += 1
            return Verification(
                method="small_model",
                model_id="synthetic-test-provider",
                model_calls=1,
                usage=Usage(input_tokens=20, output_tokens=10),
                judgment=SemanticJudgment(
                    outcome="equivalent",
                    confidence=0.9,
                    rationale="Synthetic provider classification, not a quality evaluation.",
                    old_excerpt=old.control.evidence.excerpt,
                    new_excerpt=new.control.evidence.excerpt,
                ),
            )

    runtime, directory, config = await prepare(database, tmp_path)
    if embedding_only:
        (directory / "one.md").write_text("Operators must encrypt records.")
        (directory / "two.md").write_text("Teams shall cipher documents.")
    try:
        verifier = Verifier()
        embeddings = Embeddings()
        runtime.comparisons = BenchmarkService(database, verifier, embeddings)
        first = await runtime.orchestrator().run(configuration=config)
        assert verifier.calls == 1
        assert embeddings.calls == 1
        assert any(e.statistics.get("model_calls") == 1 for e in first.executions)
        assert (
            next(
                f
                for f in await runtime.reconciliation.findings()
                if f.check_id == "semantic_duplicate"
            ).action
            == "alias"
        )
        orchestrator = runtime.orchestrator()
        check = next(
            c
            for c in orchestrator.registry.stages["enrichment"].checks
            if c.spec.id == "semantic_duplicate"
        )
        check.spec = check.spec.model_copy(update={"version": "replayed-check-version"})
        repeated = await orchestrator.run(configuration=config)
        assert repeated.run.status == "done" and verifier.calls == 1
        assert embeddings.calls == 1
        assert any(
            e.processor_id == "enrichment/semantic_duplicate" and e.status == "done"
            for e in repeated.executions
        )
        concept = (
            await runtime.knowledge.search("encrypt records" if embedding_only else "public web")
        )[0]
        assert not (await runtime.resolver.resolve_concept(concept.id)).authoritative
    finally:
        await runtime.__aexit__()


async def test_live_concurrency_cancellation_and_resume(database, tmp_path):
    runtime, _, config = await prepare(database, tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(context):
        started.set()
        await release.wait()
        return CheckResult()

    task = None
    try:
        orchestrator = runtime.orchestrator()
        enrichment = orchestrator.registry.stages["enrichment"]
        enrichment.checks = (*enrichment.checks, Check("blocking-plugin", blocked))
        task = asyncio.create_task(orchestrator.run(configuration=config))
        await asyncio.wait_for(started.wait(), 10)
        active = await orchestrator.status()
        assert active and any(
            e.processor_id == "enrichment/blocking-plugin" and e.status == "running"
            for e in active.executions
        )
        with pytest.raises(ValueError, match="lease"):
            await runtime.orchestrator().run(configuration=config)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        cancelled = await orchestrator.status(active.run.id)
        assert cancelled and cancelled.run.status == "cancelled"
        release.set()
        resumed = await orchestrator.run(resume=active.run.id)
        assert resumed.run.status == "done" and resumed.run.attempts == 2
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runtime.__aexit__()


async def test_issue_priority_scope_filter_and_review_owner_precede_limit(database):
    from typing import Literal
    from uuid import UUID

    from ekb.domain.issues import Issue
    from ekb.services.issue_service import IssueService

    examples: tuple[tuple[int, Literal["low", "critical", "high"], str], ...] = (
        (1, "low", "AWS"),
        (2, "critical", "Azure"),
        (3, "high", "AWS"),
    )
    records = tuple(
        Issue(
            id=UUID(int=i),
            issue_type="synthetic",
            severity=severity,
            confidence=0.9,
            concept_ids=(),
            scope=Scope(platform=platform),
            check_id="fixture",
            check_version="1",
            evidence_revision="fixture",
            explanation="Synthetic prioritization example.",
        )
        for i, severity, platform in examples
    )
    async with database() as uow:
        await uow.issues.observe(records, "fixture", uuid4())
        await uow.commit()
    service = IssueService(database)
    assert (await service.list(limit=1))[0].id == records[1].id
    assert (await service.list(limit=1, scope=Scope(platform="aws")))[0].id == records[2].id
    await service.review(records[2].id, "under_review", "reviewer", "Assign.", owner="owner")
    updated = await service.review(records[2].id, "dismissed", "reviewer", "Preserve assignment.")
    assert updated.issue.owner == "owner"


async def test_disabled_check_and_stage_do_not_execute(database, tmp_path):
    runtime, _, config = await prepare(database, tmp_path)
    try:
        config = config.model_copy(
            update={
                "stages": {
                    **config.stages,
                    "enrichment": StageOptions(
                        checks={"semantic_duplicate": CheckOptions(enabled=False)}
                    ),
                    "issues": StageOptions(enabled=False),
                }
            }
        )
        result = await runtime.orchestrator().run(configuration=config)
        assert result.run.status == "done"
        assert any(e.processor_id == "issues" and e.status == "skipped" for e in result.executions)
        assert not any(e.processor_id == "enrichment/semantic_duplicate" for e in result.executions)
        assert not any(f.action == "alias" for f in await runtime.reconciliation.findings())
    finally:
        await runtime.__aexit__()
