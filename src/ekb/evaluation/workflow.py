from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from ekb.adapters.persistence.postgres import Postgres
from ekb.adapters.persistence.postgres.models import Base
from ekb.config import Settings
from ekb.domain.execution import CheckOptions
from ekb.domain.models import (
    Candidate,
    Extraction,
    FetchResult,
    InferenceResult,
    Scope,
    Segment,
    SourceItem,
    SourceRecord,
    SourceRevision,
    content_hash,
    fingerprint,
    stable_id,
)
from ekb.domain.resolution import Decision, Deviation, decision_heads
from ekb.ports.workflow import CheckContext, CheckResult
from ekb.runtime import Runtime
from ekb.services.knowledge import KnowledgeService
from ekb.services.knowledge_resolver import KnowledgeResolver
from ekb.stages.indexing import IndexingStage
from ekb.stages.issues import deviation_issues

SCHEMA = "ekb_phase3_demo"
ACTOR = "synthetic-demo-reviewer"


class RetainedCorpus:
    name = "retained-source-artifacts"

    def __init__(
        self, runtime: Runtime, items: list[SourceRecord], revisions: dict[UUID, SourceRevision]
    ):
        self.runtime, self.items, self.revisions = runtime, items, revisions

    async def discover(self):
        for source in self.items:
            yield source.item

    async def fetch(self, item: SourceItem, previous: SourceRecord | None) -> FetchResult:
        return FetchResult(
            content=await self.runtime.artifacts.get(self.revisions[item.id].artifact_key)
        )


async def enterprise_fixture(runtime: Runtime, path: Path) -> Deviation:
    fixture = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    body = "# SYNTHETIC enterprise architecture fixture\n\n" + "\n\n".join(
        fact["statement"] for fact in fixture["facts"]
    )
    raw = body.encode()
    item = SourceItem(
        id=stable_id("phase3-enterprise-fixture"),
        connector="synthetic-fixture",
        location="synthetic:enterprise-architecture",
        name="Synthetic enterprise architecture",
        format="md",
        metadata={"synthetic": True},
    )
    revision = SourceRevision(
        id=stable_id(item.id, content_hash(raw)),
        source_id=item.id,
        content_hash=content_hash(raw),
        artifact_key=await runtime.artifacts.put(raw),
        source_location=item.location,
    )
    segment = Segment(
        id=stable_id(revision.id, "fixture"), revision_id=revision.id, ordinal=0, text=body
    )
    candidates = [
        Candidate(
            semantic_type=None,
            statement=fact["statement"],
            excerpt=fact["statement"],
            scope=Scope.model_validate(fact["scope"]),
            source_key=fact["id"],
            confidence=1.0,
            uncertainty="Explicitly synthetic example, not an external framework obligation.",
        )
        for fact in fixture["facts"]
    ]
    async with runtime.database() as uow:
        await uow.sources.save(SourceRecord(item=item, revision_id=revision.id))
        await uow.sources.save_revision(revision)
        await uow.sources.save_segments(revision.id, [segment], "synthetic-fixture-v1")
        ids = await uow.knowledge.persist(
            segment,
            InferenceResult(
                extraction=Extraction(candidates=candidates),
                model_id="synthetic-fixture",
                prompt_version="fixture-v1",
            ),
        )
        await uow.commit()
        evidence = await uow.knowledge.get(ids[3])
        assert evidence
    exception = fixture["exception"]
    return Deviation(
        id=stable_id("phase3-approved-exception"),
        concept_id=ids[1],
        solution=exception["solution"],
        scope=candidates[3].scope,
        approved=exception["approved"],
        effective_from=exception["effective_from"],
        effective_until=exception["effective_until"],
        evidence_occurrence_id=evidence.occurrences[0].id,
    )


async def demonstrate_workflow(
    settings: Settings,
    fixture: Path,
    output: Path,
) -> dict:
    baseline = settings.model_copy(
        update={
            "inference": "rules",
            "reconciliation_model_id": "",
            "embedding_model_id": "",
        }
    )
    async with Runtime(baseline) as original:
        async with original.database() as uow:
            items = [
                s
                for s in await uow.sources.list()
                if s.revision_id and not s.item.metadata.get("synthetic")
            ]
            revisions: dict[UUID, SourceRevision] = {}
            for source in items:
                assert source.revision_id
                revision = await uow.sources.revision(source.revision_id)
                assert revision
                revisions[source.item.id] = revision
        if not items:
            raise ValueError("Index at least one ordinary source first")
        async with original.database.engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        database = Postgres(settings.database_url.get_secret_value(), schema=SCHEMA)
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with Runtime(baseline) as runtime:
            await runtime.database.close()
            runtime.database = database
            runtime.knowledge = KnowledgeService(database)
            captured = RetainedCorpus(runtime, items, revisions)
            deviation = await enterprise_fixture(runtime, fixture)
            registry = runtime.orchestrator().registry

            async def index(context: CheckContext) -> CheckResult:
                report = await runtime.pipeline(require_complete=True).scan([captured])
                if report.failed:
                    raise ValueError("Retained corpus indexing failed")
                return CheckResult(candidates=report.concepts)

            registry.stages["indexing"] = IndexingStage(
                database, index, fingerprint(retained=[str(r.id) for r in revisions.values()])
            )
            orchestrator = runtime.orchestrator(registry)
            first = await orchestrator.run()
            selected = None
            for source in items:
                for concept in await runtime.knowledge.search(limit=50, source_id=source.item.id):
                    provenance = await runtime.knowledge.provenance(concept.id)
                    match = next(
                        (p for p in provenance if p.revision.id == revisions[source.item.id].id),
                        None,
                    )
                    if match:
                        version = next(
                            v
                            for v in concept.versions
                            if v.id == match.occurrence.concept_version_id
                        )
                        if version.publication.publication_status not in {
                            "withdrawn",
                            "superseded",
                            "deprecated",
                        }:
                            selected = concept, version
                            break
                if selected:
                    break
            if not selected:
                raise ValueError("Retained sources did not yield an evidenced current assertion")
            concept, version = selected

            async def accept(finding):
                history = await runtime.reconciliation.history(finding.id)
                if history and history[-1].outcome == "accepted":
                    return history[-1]
                return await runtime.reconciliation.decide(
                    Decision(
                        finding_id=finding.id,
                        outcome="accepted",
                        actor=ACTOR,
                        rationale="SIMULATED human review in the isolated demonstration schema; "
                        "not an operational enterprise approval.",
                        previous_id=history[-1].id if history else None,
                    )
                )

            scope = version.candidate.scope.model_copy(
                update={
                    "application": version.candidate.scope.application or "synthetic-demonstration"
                }
            )
            proposal = await runtime.reconciliation.propose_canonical(
                concept.id, version.id, scope, allow_prerelease=True, actor=ACTOR
            )
            canonical_decision = await accept(proposal)
            second = await orchestrator.run()
            restarted = KnowledgeResolver(database)
            result = await restarted.resolve_concept(concept.id, scope)
            assert result.authoritative and len(result.versions) == 1 and result.provenance
            assert (await restarted.resolve_concept(concept.id)).status == "not_established"
            async with database() as uow:
                final = await uow.resolution.snapshot()
            heads = decision_heads(final.decisions)
            assert heads[proposal.id].id == canonical_decision.id
            exceptions = deviation_issues(
                CheckContext(
                    run_id=second.run.id,
                    input_revision=final.revision,
                    snapshot=final,
                    options=CheckOptions(),
                ),
                (deviation,),
            )
            findings_by_check = {}
            for finding in final.findings:
                findings_by_check[finding.check_id] = findings_by_check.get(finding.check_id, 0) + 1
            report = {
                "schema": SCHEMA,
                "first_run": str(first.run.id),
                "rerun": str(second.run.id),
                "governance": "SIMULATED human decisions, isolated from production knowledge.",
                "actual_sources": len(items),
                "synthetic_facts": 4,
                "findings_by_check": findings_by_check,
                "issues": len(await runtime.issues.list(limit=1000)),
                "accepted_decisions_persisted": 1,
                "rerun_check_statuses": {
                    e.processor_id: e.status for e in second.executions if e.kind == "check"
                },
                "fixture_approved_exceptions": 1,
                "fixture_excessive_deviation_issues": len(exceptions.issues),
                "resolution": result.model_dump(mode="json"),
                "model_calls": sum(e.statistics.get("model_calls", 0) for e in first.executions),
            }
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, json.dumps(report, indent=2), encoding="utf-8")
    return {k: v for k, v in report.items() if k != "resolution"}
