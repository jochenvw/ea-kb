import asyncio
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from textual.widgets import Input, Static

from ekb.adapters.persistence.postgres import Postgres
from ekb.adapters.persistence.postgres.models import FindingRow
from ekb.adapters.persistence.postgres.workflow import ResolutionStore
from ekb.config import Settings
from ekb.domain.execution import CheckOptions, PipelineConfiguration, StageOptions
from ekb.domain.models import (
    ConceptVersion,
    PublicationEvidence,
    PublicationMetadata,
    Scope,
    SemanticType,
    SemanticVersion,
    Usage,
    now,
)
from ekb.domain.reconciliation import (
    CachedTraceability,
    EmbeddingResult,
    SemanticAssertion,
    SemanticJudgment,
    Verification,
    VerificationStatus,
)
from ekb.domain.resolution import Decision, active_aliases
from ekb.domain.traceability import TraceabilitySelection
from ekb.interfaces.api import create_app
from ekb.interfaces.tui import KnowledgeApp
from ekb.ports.workflow import CheckResult
from ekb.runtime import Runtime
from ekb.services.knowledge import KnowledgeService
from ekb.stages.enrichment import Check
from ekb.stages.semantic import _embeddings, _verification, _verification_key

pytestmark = pytest.mark.integration


async def test_traceability_replay_entries_satisfy_database_constraint(database):
    entry = CachedTraceability(
        selection=TraceabilitySelection(unresolved_reason="No defensible parent."),
        method="small_model",
        model_id="synthetic-model",
    )
    async with database() as uow:
        await uow.workflow.save_semantic_cache({"traceability-test": entry})
        await uow.commit()
    async with database() as uow:
        assert await uow.workflow.semantic_cache(["traceability-test"]) == {
            "traceability-test": entry
        }


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
    config = PipelineConfiguration(stages={"indexing": StageOptions(directory=str(directory))})
    return runtime, directory, config


@pytest.mark.parametrize("status", ["working_draft", "consultation", "proposed", "preview"])
async def test_publication_changes_invalidate_snapshot_and_guard_adoption(
    database, tmp_path, status
):
    runtime, directory, _ = await prepare(database, tmp_path)
    try:
        await runtime.pipeline().scan(runtime.connectors(directory))
        concept = (await runtime.knowledge.search("public web endpoints"))[0]
        provenance = (await runtime.knowledge.provenance(concept.id))[0]
        async with database() as uow:
            before = await uow.resolution.snapshot()
            source = await uow.sources.get(provenance.source.item.id)
            assert source and source.revision_id
            item = source.item.model_copy(
                update={"metadata": {**source.item.metadata, "publication_status": status}}
            )
            await uow.sources.save(source.model_copy(update={"item": item}))
            await uow.sources.save_publication(
                source.revision_id,
                PublicationMetadata(
                    publication_status=status,
                    evidence=(PublicationEvidence(field="publication_status", excerpt=status),),
                ),
                "status-update",
            )
            await uow.commit()
        async with database() as uow:
            after = await uow.resolution.snapshot()
        version_id = provenance.occurrence.concept_version_id
        assert after.revision != before.revision
        assert version_id not in before.preview_version_ids
        assert version_id in after.preview_version_ids
        proposal = await runtime.reconciliation.propose_canonical(
            concept.id, version_id, Scope(application="pilot"), actor="test"
        )
        with pytest.raises(ValueError, match="limited-scope approval"):
            await runtime.reconciliation.decide(
                Decision(
                    finding_id=proposal.id,
                    outcome="accepted",
                    actor="test",
                    rationale="Review required for a draft.",
                )
            )
    finally:
        await runtime.__aexit__()


async def test_workflow_evaluation_uses_retained_ordinary_sources_offline(
    database, tmp_path, monkeypatch
):
    from ekb.evaluation import workflow

    runtime, directory, _ = await prepare(database, tmp_path)
    demo_schema = f"ekb_demo_test_{uuid4().hex}"
    async with database.engine.connect() as connection:
        source_schema = await connection.scalar(text("SELECT current_schema()"))
    monkeypatch.setattr("ekb.runtime.Postgres", lambda url: Postgres(url, schema=source_schema))
    monkeypatch.setattr(workflow, "SCHEMA", demo_schema)
    try:
        scan = await runtime.pipeline().scan(runtime.connectors(directory))
        assert scan.failed == 0 and scan.processed == 2
        settings = runtime.settings.model_copy(
            update={"database_url": SecretStr(database.engine.url.render_as_string(False))}
        )
        output = tmp_path / "workflow.json"
        report = await workflow.demonstrate_workflow(
            settings, Path("tests") / "fixtures" / "enterprise.json", output
        )
        assert report["actual_sources"] == 2 and report["model_calls"] == 0
        assert report["accepted_decisions_persisted"] == 1
        assert report["fixture_excessive_deviation_issues"] == 0
        assert report["governance"].startswith("SIMULATED")
        assert output.is_file()
    finally:
        await runtime.__aexit__()
        async with database.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{demo_schema}" CASCADE'))


async def test_workflow_evaluation_rejects_an_incomplete_leased_source(
    database, tmp_path, monkeypatch
):
    from ekb.adapters.persistence.postgres.models import Base
    from ekb.domain.models import ProcessingTask, stable_id
    from ekb.evaluation import workflow

    monkeypatch.setenv("EKB_INFERENCE", "rules")
    monkeypatch.setenv("EKB_RECONCILIATION_MODEL_ID", "")
    monkeypatch.setenv("EKB_EMBEDDING_MODEL_ID", "")
    runtime, directory, _ = await prepare(database, tmp_path)
    demo_schema = f"ekb_demo_test_{uuid4().hex}"
    url = database.engine.url.render_as_string(False)
    async with database.engine.connect() as connection:
        source_schema = await connection.scalar(text("SELECT current_schema()"))
    monkeypatch.setattr("ekb.runtime.Postgres", lambda url: Postgres(url, schema=source_schema))
    monkeypatch.setattr(workflow, "SCHEMA", demo_schema)
    demo = Postgres(url, schema=demo_schema)
    try:
        assert (await runtime.pipeline().scan(runtime.connectors(directory))).processed == 2
        async with database() as uow:
            source = (await uow.sources.list())[0]
            assert source.revision_id is not None
            revision = await uow.sources.revision(source.revision_id)
            assert revision is not None
        async with database.engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{demo_schema}"'))
        async with demo.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        pipeline = runtime.pipeline()
        processing_fingerprint = pipeline._processing_fingerprint(
            revision.content_hash, source.item
        )
        task = ProcessingTask(
            id=stable_id(revision.id, processing_fingerprint),
            revision_id=revision.id,
            fingerprint=processing_fingerprint,
        )
        async with demo() as uow:
            await uow.sources.save(source)
            await uow.sources.save_revision(revision)
            assert await uow.processing.claim(task, uuid4())
            await uow.commit()
        settings = runtime.settings.model_copy(update={"database_url": SecretStr(url)})
        output = tmp_path / "workflow.json"
        with pytest.raises(ExceptionGroup) as error:
            await workflow.demonstrate_workflow(
                settings, Path("tests") / "fixtures" / "enterprise.json", output
            )
        assert error.group_contains(ValueError, match="Retained corpus indexing failed")
        assert not output.exists()
        async with demo() as uow:
            current = await uow.processing.task(revision.id, processing_fingerprint)
        assert current and current.status == "running"
    finally:
        await demo.close()
        await runtime.__aexit__()
        async with database.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{demo_schema}" CASCADE'))


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
                effective_at=date(2020, 1, 1),
                effective_until=date(2030, 12, 31),
            )
        )
        assert await runtime.reconciliation.decide(accepted) == accepted
        source, target = duplicate.concept_ids
        async with database() as uow:
            snapshot = await uow.resolution.snapshot()
        assert active_aliases(snapshot, date(2019, 1, 1)) == {}
        assert active_aliases(snapshot, date(2021, 1, 1)) == {source: target}
        assert active_aliases(snapshot, date(2031, 1, 1)) == {}
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
                effective_at=date(2022, 1, 1),
            )
        )
        assert (await restarted.resolve_concept(source, scope)).status == "not_established"
        assert len(await runtime.reconciliation.history(duplicate.id)) == 2
        async with database() as uow:
            snapshot = await uow.resolution.snapshot()
        assert active_aliases(snapshot, date(2021, 1, 1)) == {source: target}
        assert active_aliases(snapshot, date(2023, 1, 1)) == {}
        assert await runtime.knowledge.provenance(source)
    finally:
        await runtime.__aexit__()


async def test_concurrent_alias_reviews_cannot_accept_a_cycle(database, tmp_path):
    runtime, _, config = await prepare(database, tmp_path)
    try:
        await runtime.orchestrator().run(configuration=config)
        forward = next(f for f in await runtime.reconciliation.findings() if f.action == "alias")
        reverse = forward.model_copy(
            update={"id": uuid4(), "concept_ids": tuple(reversed(forward.concept_ids))}
        )
        await runtime.reconciliation.propose(reverse)
        outcomes = await asyncio.gather(
            *(
                runtime.reconciliation.decide(
                    Decision(
                        finding_id=finding.id,
                        outcome="accepted",
                        actor="reviewer",
                        rationale="Review of synthetic identity evidence.",
                    )
                )
                for finding in (forward, reverse)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(outcome, Decision) for outcome in outcomes) == 1
        failures = [outcome for outcome in outcomes if isinstance(outcome, ValueError)]
        assert len(failures) == 1 and "Cyclic" in str(failures[0])
    finally:
        await runtime.__aexit__()


async def test_alias_target_can_change_only_after_explicit_reversal(database, tmp_path):
    runtime, directory, config = await prepare(database, tmp_path)
    (directory / "three.md").write_text(
        "# Synthetic enterprise standard\n\nOperators must protect internal web endpoints with WAF."
    )
    try:
        await runtime.orchestrator().run(configuration=config)
        forward = next(f for f in await runtime.reconciliation.findings() if f.action == "alias")
        source, target = forward.concept_ids
        concepts = await runtime.knowledge.search("web")
        replacement = next(concept.id for concept in concepts if concept.id not in {source, target})
        source_concept = await runtime.knowledge.get(source)
        assert source_concept is not None
        alternative = forward.model_copy(
            update={
                "id": uuid4(),
                "concept_ids": (source, replacement),
                "version_ids": (),
                "occurrence_ids": (source_concept.occurrences[0].id,),
            }
        )
        await runtime.reconciliation.propose(alternative)
        accepted = await runtime.reconciliation.decide(
            Decision(
                finding_id=forward.id,
                outcome="accepted",
                actor="reviewer",
                rationale="Initial synthetic identity review.",
            )
        )
        change = Decision(
            finding_id=alternative.id,
            outcome="accepted",
            actor="reviewer",
            rationale="Replacement synthetic identity review.",
        )
        with pytest.raises(ValueError, match="Reverse the existing alias"):
            await runtime.reconciliation.decide(change)
        await runtime.reconciliation.decide(
            Decision(
                finding_id=forward.id,
                previous_id=accepted.id,
                outcome="reversed",
                actor="reviewer",
                rationale="Reverse the initial target before replacing it.",
            )
        )
        await runtime.reconciliation.decide(change)
        async with database() as uow:
            snapshot = await uow.resolution.snapshot()
        assert active_aliases(snapshot, now().date())[source] == replacement
    finally:
        await runtime.__aexit__()


async def test_alias_snapshot_stays_consistent_with_concurrent_review(
    database, tmp_path, monkeypatch
):
    runtime, _, config = await prepare(database, tmp_path)
    snapshot_task = None
    release = asyncio.Event()
    try:
        await runtime.orchestrator().run(configuration=config)
        candidate = next(f for f in await runtime.reconciliation.findings() if f.action == "alias")
        candidate = candidate.model_copy(update={"id": uuid4(), "check_id": "manual"})
        findings_read = asyncio.Event()
        original_bounded = ResolutionStore._bounded

        async def pause_after_findings(store, model):
            rows = await original_bounded(store, model)
            if model is FindingRow:
                findings_read.set()
                await release.wait()
            return rows

        monkeypatch.setattr(ResolutionStore, "_bounded", pause_after_findings)

        async def capture():
            async with database() as uow:
                return await uow.resolution.snapshot()

        snapshot_task = asyncio.create_task(capture())
        await asyncio.wait_for(findings_read.wait(), timeout=10)
        await runtime.reconciliation.propose(candidate)
        decision = await runtime.reconciliation.decide(
            Decision(
                finding_id=candidate.id,
                outcome="accepted",
                actor="reviewer",
                rationale="Concurrent synthetic identity review.",
            )
        )
        release.set()
        snapshot = await asyncio.wait_for(snapshot_task, timeout=10)
        assert decision.id not in {item.id for item in snapshot.decisions}
        assert candidate.concept_ids[0] not in active_aliases(snapshot, now().date())
        monkeypatch.setattr(ResolutionStore, "_bounded", original_bounded)
        async with database() as uow:
            snapshot = await uow.resolution.snapshot()
        assert (
            active_aliases(snapshot, now().date())[candidate.concept_ids[0]]
            == (candidate.concept_ids[1])
        )
    finally:
        release.set()
        if snapshot_task and not snapshot_task.done():
            await snapshot_task
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


class ReplayEmbeddings:
    fingerprint = "synthetic-embeddings-v1"
    model_id = "synthetic-test-embeddings"

    def __init__(self):
        self.calls = 0
        self.unavailable = False
        self.batches = []

    async def embed(self, texts):
        if self.unavailable:
            raise RuntimeError("Embedding provider unavailable")
        self.calls += 1
        self.batches.append(tuple(texts))
        return EmbeddingResult(
            vectors=tuple((1.0, 0.0) for _ in texts),
            calls=1,
            input_tokens=8,
            usage_complete=True,
            cost=0.25,
        )

    async def close(self):
        pass


class ReplayVerifier:
    fingerprint = "synthetic-verifier-v1"

    def __init__(self):
        self.calls = 0
        self.unavailable = False
        self.method = "small_model"
        self.status: VerificationStatus = "completed"
        self.ungrounded = False

    async def verify(self, old, new):
        if self.unavailable:
            raise RuntimeError("Verification provider unavailable")
        self.calls += 1
        return Verification(
            method=self.method,
            status=self.status,
            model_id="synthetic-test-provider",
            model_calls=1,
            usage=Usage(input_tokens=20, output_tokens=10, cost=0.5),
            judgment=SemanticJudgment(
                outcome="equivalent",
                confidence=0.9,
                rationale="Synthetic provider classification, not a quality evaluation.",
                old_excerpt="fabricated" if self.ungrounded else old.excerpt,
                new_excerpt=new.excerpt,
            ),
        )

    async def close(self):
        pass


def semantic_execution(run):
    return next(e for e in run.executions if e.processor_id == "enrichment/semantic_duplicate")


@pytest.mark.parametrize("embedding_only", [False, True])
async def test_optional_semantic_provider_reuses_completed_checks_and_preserves_proposals(
    database,
    tmp_path,
    embedding_only,
):

    runtime, directory, config = await prepare(database, tmp_path)
    if embedding_only:
        (directory / "one.md").write_text("Operators must encrypt records.")
        (directory / "two.md").write_text("Teams shall cipher documents.")
    try:
        verifier = ReplayVerifier()
        if embedding_only:
            verifier.method = "independent.semantic-v1"
        embeddings = ReplayEmbeddings()
        runtime.verifier = verifier
        runtime.embeddings = embeddings
        first = await runtime.orchestrator().run(configuration=config)
        assert verifier.calls == 1
        assert embeddings.calls == 1
        first_semantic = semantic_execution(first)
        assert first_semantic.statistics["model_calls"] == 1
        assert first_semantic.statistics["embedding_calls"] == 1
        assert first_semantic.statistics["model_cost"] == pytest.approx(0.75)
        assert (
            next(
                f
                for f in await runtime.reconciliation.findings()
                if f.check_id == "semantic_duplicate"
            ).action
            == "alias"
        )
        orchestrator = runtime.orchestrator()
        repeated = await orchestrator.run(configuration=config)
        assert repeated.run.status == "done" and verifier.calls == 1
        assert embeddings.calls == 1
        assert any(
            e.processor_id == "enrichment/semantic_duplicate" and e.status == "skipped"
            for e in repeated.executions
        )
        check = next(
            c
            for c in orchestrator.registry.stages["enrichment"].checks
            if c.spec.id == "semantic_duplicate"
        )
        if embedding_only:
            async with database.engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE semantic_replay_cache SET payload = payload - 'status' "
                        "WHERE kind = 'verification'"
                    )
                )
        check.spec = check.spec.model_copy(update={"version": "replayed-check-version"})
        embeddings.unavailable = verifier.unavailable = True
        repeated = await orchestrator.run(configuration=config)
        replay = semantic_execution(repeated)
        assert repeated.run.status == "done" and verifier.calls == embeddings.calls == 1
        assert replay.status == "done"
        assert replay.statistics["model_calls"] == replay.statistics["embedding_calls"] == 0
        assert replay.statistics["input_tokens"] == replay.statistics["output_tokens"] == 0
        assert "model_cost" not in replay.statistics

        pages = []
        cursor = None
        async with database() as uow:
            while True:
                page = await uow.inspection.rows("semantic_replay_cache", limit=1, after=cursor)
                pages.extend(page.rows)
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
        assert len(pages) == 3 and len({row["id"] for row in pages}) == 3
        assert {row["kind"] for row in pages} == {"embedding", "verification"}
        assert (
            next(row for row in pages if row["kind"] == "verification")["payload"]["method"]
            == verifier.method
        )

        if not embedding_only:
            embeddings.unavailable = verifier.unavailable = False
            verifier.fingerprint = "synthetic-verifier-v2"
            changed_verifier = await runtime.orchestrator().run(configuration=config)
            assert semantic_execution(changed_verifier).statistics["model_calls"] == 1
            assert embeddings.calls == 1 and verifier.calls == 2
            embeddings.fingerprint = "synthetic-embeddings-v2"
            changed_embedding = await runtime.orchestrator().run(configuration=config)
            assert semantic_execution(changed_embedding).statistics["embedding_calls"] == 1
            assert embeddings.calls == 2 and verifier.calls == 2

            (directory / "two.md").write_text(
                "# Synthetic enterprise guideline\n\n"
                "Operators must protect external web endpoints with filters."
            )
            changed_input = await runtime.orchestrator().run(configuration=config)
            assert changed_input.run.status == "done"
            assert semantic_execution(changed_input).statistics["model_calls"] == 1
            assert embeddings.calls == verifier.calls == 3
            assert len(embeddings.batches[-1]) == 1

        concept = (
            await runtime.knowledge.search("encrypt records" if embedding_only else "public web")
        )[0]
        assert not (await runtime.resolver.resolve_concept(concept.id)).authoritative
    finally:
        await runtime.__aexit__()


@pytest.mark.parametrize(
    "kind,path,replacement",
    [
        ("embedding", "{vector}", [0, 0]),
        ("embedding", "{vector}", ["NaN", 1]),
        ("embedding", "{vector}", [1, 0, 0]),
        ("verification", "{judgment,confidence}", 2),
        ("verification", "{judgment,old_excerpt}", "fabricated evidence"),
        ("verification", "{method}", "model_failed"),
        ("verification", "{status}", "failed"),
    ],
    ids=[
        "zero-vector",
        "nonfinite",
        "dimensions",
        "invalid",
        "ungrounded",
        "failed-method",
        "failed-status",
    ],
)
async def test_postgres_replay_recovers_invalid_cached_data(
    database, tmp_path, kind, path, replacement
):
    runtime, _, config = await prepare(database, tmp_path)
    verifier, embeddings = ReplayVerifier(), ReplayEmbeddings()
    runtime.verifier, runtime.embeddings = verifier, embeddings
    try:
        orchestrator = runtime.orchestrator()
        first = await orchestrator.run(configuration=config)
        assert first.run.status == "done" and verifier.calls == embeddings.calls == 1
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE semantic_replay_cache "
                    "SET payload = jsonb_set(payload, CAST(:path AS text[]), "
                    "CAST(:replacement AS jsonb)) "
                    "WHERE id = (SELECT id FROM semantic_replay_cache "
                    "WHERE kind = :kind ORDER BY id LIMIT 1)"
                ),
                {
                    "path": path.strip("{}").split(","),
                    "replacement": json.dumps(replacement),
                    "kind": kind,
                },
            )
        check = next(
            check
            for check in orchestrator.registry.stages["enrichment"].checks
            if check.spec.id == "semantic_duplicate"
        )
        check.spec = check.spec.model_copy(update={"version": "repair-cache"})
        repaired = await orchestrator.run(configuration=config)
        execution = semantic_execution(repaired)
        assert repaired.run.status == "done"
        assert embeddings.calls == (2 if kind == "embedding" else 1)
        assert verifier.calls == (2 if kind == "verification" else 1)
        assert execution.statistics["model_cost"] == pytest.approx(
            0.25 if kind == "embedding" else 0.5
        )

        embeddings.unavailable = verifier.unavailable = True
        check.spec = check.spec.model_copy(update={"version": "replay-repaired-cache"})
        replay = await orchestrator.run(configuration=config)
        assert replay.run.status == "done"
        assert semantic_execution(replay).statistics["model_calls"] == 0
        assert semantic_execution(replay).statistics["embedding_calls"] == 0
    finally:
        await runtime.__aexit__()


@pytest.mark.parametrize(
    "failure",
    ["legacy-failed", "legacy-context", "provider-failed", "provider-context", "ungrounded"],
)
async def test_postgres_never_persists_failed_or_ungrounded_verification(database, failure):
    verifier = ReplayVerifier()
    verifier.method = {
        "legacy-failed": "model_failed",
        "legacy-context": "context_limit",
    }.get(failure, "independent.semantic-v1")
    if failure == "provider-failed":
        verifier.status = "failed"
    elif failure == "provider-context":
        verifier.status = "context_limit"
    verifier.ungrounded = failure == "ungrounded"
    old = SemanticAssertion(
        occurrence_id=uuid4(),
        statement="Protect endpoints.",
        excerpt="Protect endpoints.",
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
    )
    new = SemanticAssertion(
        occurrence_id=uuid4(),
        statement="Protect external endpoints.",
        excerpt="Protect external endpoints.",
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
    )
    with pytest.raises(ValueError):
        await _verification(database, verifier, old, new, {})
    async with database() as uow:
        assert await uow.workflow.semantic_cache([_verification_key(verifier, old, new)]) == {}


async def test_missing_replay_table_fails_safely_and_initialize_preserves_checkpoints(
    database, tmp_path
):
    runtime, directory, _ = await prepare(database, tmp_path)
    embeddings = ReplayEmbeddings()
    try:
        report = await runtime.pipeline().scan(runtime.connectors(directory))
        assert report.processed == 2
        async with database() as uow:
            source = (await uow.sources.list())[0]
            assert source.revision_id is not None
            revision = await uow.sources.revision(source.revision_id)
            assert revision is not None
        artifact = await runtime.artifacts.get(revision.artifact_key)

        tables = ("processing_tasks", "parse_checkpoints", "segment_results")
        async with database.engine.begin() as connection:
            before = {
                table: await connection.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in tables
            }
            assert all(before.values())
            await connection.execute(text("DROP TABLE semantic_replay_cache"))
        with pytest.raises(DBAPIError):
            await _embeddings(database, embeddings, ["Synthetic"])
        assert embeddings.calls == 0

        await database.initialize()
        async with database.engine.connect() as connection:
            after = {
                table: await connection.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in tables
            }
            assert await connection.scalar(text("SELECT count(*) FROM semantic_replay_cache")) == 0
        assert after == before
        assert await runtime.artifacts.get(revision.artifact_key) == artifact
    finally:
        await runtime.__aexit__()


async def test_unknown_semantic_judgment_does_not_invent_a_distinction(database, tmp_path):
    from ekb.domain.reconciliation import SemanticJudgment, Verification

    class UnknownVerifier:
        fingerprint = "synthetic-unknown-verifier"

        def __init__(self):
            self.calls = 0

        async def verify(self, old, new):
            self.calls += 1
            return Verification(
                method="small_model",
                model_calls=1,
                judgment=SemanticJudgment(
                    outcome="unknown",
                    confidence=0.1,
                    rationale="Insufficient evidence to classify this pair.",
                    old_excerpt=old.excerpt,
                    new_excerpt=new.excerpt,
                ),
            )

        async def close(self):
            pass

    runtime, _, config = await prepare(database, tmp_path)
    verifier = UnknownVerifier()
    runtime.verifier = verifier
    try:
        orchestrator = runtime.orchestrator()
        run = await orchestrator.run(configuration=config)
        semantic = next(
            e for e in run.executions if e.processor_id == "enrichment/semantic_duplicate"
        )
        assert semantic.statistics["model_calls"] == 1
        assert not [
            f for f in await runtime.reconciliation.findings() if f.check_id == "semantic_duplicate"
        ]
        check = next(
            check
            for check in orchestrator.registry.stages["enrichment"].checks
            if check.spec.id == "semantic_duplicate"
        )
        check.spec = check.spec.model_copy(update={"version": "replay-unknown"})
        replay = await orchestrator.run(configuration=config)
        assert semantic_execution(replay).statistics["model_calls"] == 0
        assert verifier.calls == 1
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
