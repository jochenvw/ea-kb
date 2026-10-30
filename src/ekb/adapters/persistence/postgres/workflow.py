from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ekb.adapters.persistence.postgres.models import (
    AuthorityRow,
    BenchmarkDocumentRow,
    ConceptRow,
    DecisionRow,
    ExecutionRow,
    FindingRow,
    OccurrenceRow,
    RelationshipRow,
    RevisionRow,
    SegmentRow,
    SourceRow,
    VersionRow,
    WorkflowLeaseRow,
)
from ekb.domain.execution import Execution
from ekb.domain.models import (
    AuthorityEvent,
    ConceptDetail,
    ConceptVersion,
    Occurrence,
    Relationship,
    fingerprint,
    now,
    stable_id,
)
from ekb.domain.resolution import (
    Decision,
    Deviation,
    Finding,
    KnowledgeSnapshot,
    alias_edges,
    decision_heads,
    resolve_alias,
    version_evidence,
)


async def governance_lock(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(735904214)"))


class Workflow:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_execution(self, execution: Execution) -> None:
        if execution.owner:
            lease = await self.session.scalar(
                select(WorkflowLeaseRow)
                .where(WorkflowLeaseRow.name == "knowledge-pipeline")
                .with_for_update()
            )
            if not lease or lease.owner != execution.owner or lease.expires_at <= now():
                raise RuntimeError("Stale pipeline execution owner")
        values = dict(
            id=execution.id,
            parent_id=execution.parent_id,
            kind=execution.kind,
            processor_id=execution.processor_id,
            fingerprint=execution.fingerprint,
            status=execution.status,
            started_at=execution.started_at,
            payload=execution.model_dump(mode="json"),
        )
        statement = insert(ExecutionRow).values(**values)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["id"], set_={k: v for k, v in values.items() if k != "id"}
            )
        )

    async def execution(self, execution_id: UUID) -> Execution | None:
        row = await self.session.get(ExecutionRow, execution_id)
        return Execution.model_validate(row.payload) if row else None

    async def executions(self, parent_id: UUID | None = None, limit: int = 100) -> list[Execution]:
        statement = select(ExecutionRow).order_by(ExecutionRow.started_at.desc(), ExecutionRow.id)
        statement = statement.where(ExecutionRow.parent_id == parent_id)
        rows = await self.session.scalars(statement.limit(min(limit, 1000)))
        return [Execution.model_validate(row.payload) for row in rows]

    async def successful(
        self, processor_id: str, fingerprint: str | None = None
    ) -> Execution | None:
        statement = select(ExecutionRow).where(
            ExecutionRow.processor_id == processor_id, ExecutionRow.status == "done"
        )
        if fingerprint:
            statement = statement.where(ExecutionRow.fingerprint == fingerprint)
        row = await self.session.scalar(
            statement.order_by(ExecutionRow.started_at.desc(), ExecutionRow.id).limit(1)
        )
        return Execution.model_validate(row.payload) if row else None

    async def lease(self, name: str, owner: UUID, seconds: int = 120) -> bool:
        current = now()
        statement = insert(WorkflowLeaseRow).values(
            name=name, owner=owner, expires_at=current + timedelta(seconds=seconds)
        )
        result = await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["name"],
                set_={"owner": owner, "expires_at": current + timedelta(seconds=seconds)},
                where=or_(WorkflowLeaseRow.owner == owner, WorkflowLeaseRow.expires_at <= current),
            ).returning(WorkflowLeaseRow.name)
        )
        return result.scalar_one_or_none() is not None

    async def release(self, name: str, owner: UUID) -> None:
        from sqlalchemy import delete

        await self.session.execute(
            delete(WorkflowLeaseRow).where(
                WorkflowLeaseRow.name == name, WorkflowLeaseRow.owner == owner
            )
        )


class ResolutionStore:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _bounded(self, model):
        rows = list(await self.session.scalars(select(model).limit(20001)))
        if len(rows) > 20000:
            raise ValueError("Knowledge workspace exceeds 20,000 records; partition the corpus")
        return rows

    async def snapshot(self) -> KnowledgeSnapshot:
        versions = [
            ConceptVersion.model_validate(r.payload) for r in await self._bounded(VersionRow)
        ]
        occurrences = [
            Occurrence.model_validate(r.payload) for r in await self._bounded(OccurrenceRow)
        ]
        relationships = [
            Relationship.model_validate(r.payload) for r in await self._bounded(RelationshipRow)
        ]
        authority = [
            AuthorityEvent.model_validate(r.payload) for r in await self._bounded(AuthorityRow)
        ]
        sources = await self._bounded(SourceRow)
        segments = await self._bounded(SegmentRow)
        current_revisions = {
            r.payload.get("revision_id") for r in sources if r.payload.get("status") == "available"
        }
        current_segments = {r.id for r in segments if str(r.revision_id) in current_revisions}
        current_versions = frozenset(
            o.concept_version_id for o in occurrences if o.segment_id in current_segments
        )
        benchmarks = [
            r
            for r in await self._bounded(BenchmarkDocumentRow)
            if str(r.revision_id) in current_revisions
        ]
        edition_status = {}
        for row in sorted(benchmarks, key=lambda r: (r.recorded_at, str(r.id))):
            if row.role == "overview":
                edition_status[row.edition] = row.payload["document"]["publication"][
                    "publication_status"
                ]
        preview_segments = {
            r.segment_id
            for r in benchmarks
            if edition_status.get(r.edition)
            in {"preview", "draft", "alpha", "beta", "release_candidate"}
        }
        concepts = []
        for cid in sorted({v.concept_id for v in versions}, key=str):
            own_versions = sorted(
                [v for v in versions if v.concept_id == cid],
                key=lambda v: (v.recorded_at, str(v.id)),
            )
            ids = {v.id for v in own_versions}
            concepts.append(
                ConceptDetail(
                    id=cid,
                    versions=own_versions,
                    occurrences=[o for o in occurrences if o.concept_version_id in ids],
                    relationships=[
                        r for r in relationships if cid in {r.from_concept_id, r.to_concept_id}
                    ],
                    authority_events=[
                        e.model_dump(mode="json") for e in authority if e.concept_version_id in ids
                    ],
                )
            )
        # A visible decision's immutable finding must also be visible in this snapshot.
        decisions = tuple(await self.decisions())
        findings = tuple(Finding.model_validate(r.payload) for r in await self._bounded(FindingRow))
        enrichment = await Workflow(self.session).successful("enrichment")
        active = set(enrichment.output.record_ids) if enrichment and enrichment.output else set()
        active.update(f.id for f in findings if f.check_id == "manual")
        return KnowledgeSnapshot(
            revision=fingerprint(
                sources=sorted(
                    (
                        str(r.id),
                        r.payload.get("revision_id"),
                        r.payload["item"].get("metadata", {}).get("framework_namespace"),
                        r.payload["item"].get("metadata", {}).get("framework_revision"),
                    )
                    for r in sources
                ),
                versions=sorted(str(v.id) for v in versions),
                occurrences=sorted(str(o.id) for o in occurrences),
                relationships=sorted(str(r.id) for r in relationships),
            ),
            governance_revision=fingerprint(
                authority=sorted(str(e.id) for e in authority),
                decisions=sorted(str(d.id) for d in decisions),
            ),
            concepts=tuple(concepts),
            current_version_ids=current_versions,
            findings=findings,
            decisions=decisions,
            active_finding_ids=frozenset(active),
            preview_version_ids=frozenset(
                o.concept_version_id for o in occurrences if o.segment_id in preview_segments
            ),
        )

    async def save_finding(self, finding: Finding) -> None:
        if not finding.concept_ids:
            raise ValueError("A finding needs affected concepts")
        for cid in finding.concept_ids:
            if await self.session.get(ConceptRow, cid) is None:
                raise ValueError("Finding refers to an unknown concept")
        for vid in finding.version_ids:
            row = await self.session.get(VersionRow, vid)
            if row is None or row.concept_id not in finding.concept_ids:
                raise ValueError("Finding version must belong to an affected concept")
        for oid in finding.occurrence_ids:
            occurrence = await self.session.get(OccurrenceRow, oid)
            version = (
                await self.session.get(VersionRow, occurrence.concept_version_id)
                if occurrence
                else None
            )
            if version is None or version.concept_id not in finding.concept_ids:
                raise ValueError("Finding evidence must belong to an affected concept")
        for passage in finding.passages:
            rows = await self.session.scalars(
                select(SegmentRow)
                .join(RevisionRow)
                .where(
                    RevisionRow.source_id == passage.source_id,
                    RevisionRow.content_hash == passage.content_hash,
                )
            )
            if not any(
                r.payload["text"][passage.start : passage.end] == passage.excerpt for r in rows
            ):
                raise ValueError("Published relationship evidence must match a stored segment")
        if not finding.occurrence_ids and not finding.passages:
            raise ValueError("Finding requires source evidence")
        await self.session.execute(
            insert(FindingRow)
            .values(
                id=finding.id,
                check_id=finding.check_id,
                input_revision=finding.input_revision,
                payload=finding.model_dump(mode="json"),
            )
            .on_conflict_do_nothing()
        )

    async def finding(self, finding_id: UUID) -> Finding | None:
        row = await self.session.get(FindingRow, finding_id)
        return Finding.model_validate(row.payload) if row else None

    async def findings(
        self,
        limit: int = 100,
        *,
        check_id: str | None = None,
        after: UUID | None = None,
    ) -> list[Finding]:
        statement = select(FindingRow).order_by(FindingRow.id)
        if check_id:
            statement = statement.where(FindingRow.check_id == check_id)
        if after:
            statement = statement.where(FindingRow.id > after)
        rows = await self.session.scalars(statement.limit(min(limit, 1000)))
        return [Finding.model_validate(row.payload) for row in rows]

    async def decisions(self, finding_id: UUID | None = None) -> list[Decision]:
        query = select(DecisionRow).order_by(DecisionRow.recorded_at, DecisionRow.id)
        if finding_id:
            query = query.where(DecisionRow.finding_id == finding_id)
        rows = list(await self.session.scalars(query.limit(20001)))
        if len(rows) > 20000:
            raise ValueError("Decision workspace requires partitioning")
        return [Decision.model_validate(r.payload) for r in rows]

    async def apply_decision(self, decision: Decision) -> Decision:
        await governance_lock(self.session)
        stored = await self.session.get(DecisionRow, decision.id)
        if stored:
            existing = Decision.model_validate(stored.payload)
            if existing.model_dump(exclude={"recorded_at"}) != decision.model_dump(
                exclude={"recorded_at"}
            ):
                raise ValueError("Decision IDs are immutable idempotency keys")
            return existing
        finding = await self.finding(decision.finding_id)
        if finding is None:
            raise ValueError("Unknown reconciliation finding")
        previous = await self.decisions(finding.id)
        head = previous[-1] if previous else None
        if decision.previous_id != (head.id if head else None):
            raise ValueError("Decision changed concurrently; reload its latest history")
        if head and head.outcome == "accepted" and decision.outcome in {"rejected", "deferred"}:
            raise ValueError("Reverse an accepted decision before rejecting or deferring it")
        if decision.actor_kind == "model" and decision.outcome == "accepted":
            raise ValueError("A model cannot accept governed reconciliation")
        if decision.actor_kind == "rule" and (
            decision.policy != "exact-identity-v1"
            or not finding.deterministic
            or finding.action != "recognize_identity"
        ):
            raise ValueError("Automatic acceptance requires the explicit exact-identity policy")
        if decision.actor_kind == "rule" and decision.outcome == "accepted":
            identities = finding.concept_ids
            if (
                len(identities) != 1
                or len(finding.version_ids) != 1
                or len(set(finding.occurrence_ids)) < 2
            ):
                raise ValueError("Exact-identity policy requires repeated evidence of one version")
            for oid in finding.occurrence_ids:
                occurrence = await self.session.get(OccurrenceRow, oid)
                if occurrence is None or occurrence.concept_version_id != finding.version_ids[0]:
                    raise ValueError("Exact-identity occurrences must share the same version")
        if decision.outcome == "accepted":
            await self._validate_acceptance(finding, decision)
        await self.session.execute(
            insert(DecisionRow).values(
                id=decision.id,
                finding_id=finding.id,
                previous_id=decision.previous_id,
                outcome=decision.outcome,
                recorded_at=decision.recorded_at,
                payload=decision.model_dump(mode="json"),
            )
        )
        if finding.action == "canonical_review":
            await self._authority(finding, decision)
        return decision

    async def _validate_acceptance(self, finding: Finding, decision: Decision) -> None:
        if finding.action == "alias":
            if len(finding.concept_ids) != 2 or finding.concept_ids[0] == finding.concept_ids[1]:
                raise ValueError("Aliases require two different identities")
            types = []
            for concept_id in finding.concept_ids:
                types.append(
                    await self.session.scalar(
                        select(VersionRow.semantic_type)
                        .where(VersionRow.concept_id == concept_id)
                        .order_by(VersionRow.payload["recorded_at"].astext.desc(), VersionRow.id)
                        .limit(1)
                    )
                )
            if types[0] != types[1]:
                raise ValueError("Different ontology classes cannot be merged")
            head = (
                select(DecisionRow.id)
                .where(DecisionRow.finding_id == FindingRow.id)
                .order_by(DecisionRow.recorded_at.desc(), DecisionRow.id.desc())
                .correlate(FindingRow)
                .limit(1)
                .scalar_subquery()
            )
            rows = list(
                await self.session.scalars(
                    select(FindingRow)
                    .join(DecisionRow, DecisionRow.id == head)
                    .where(
                        text("(enrichment_findings.payload ->> 'action') = 'alias'"),
                        DecisionRow.outcome == "accepted",
                    )
                    .limit(20001)
                )
            )
            if len(rows) > 20000:
                raise ValueError("Alias workspace exceeds 20,000 records; partition the corpus")
            edges = alias_edges(Finding.model_validate(row.payload) for row in rows)
            if finding.concept_ids[0] in edges and (
                edges[finding.concept_ids[0]] != finding.concept_ids[1]
            ):
                raise ValueError("Reverse the existing alias decision before changing its target")
            edges[finding.concept_ids[0]] = finding.concept_ids[1]
            for identity in edges:
                resolve_alias(identity, edges)
        if finding.relationship and len(finding.concept_ids) < 2:
            raise ValueError("A relationship needs two concept identities")
        if finding.action == "version" and len(finding.concept_ids) == 2:
            findings = {
                f.id: f
                for r in await self._bounded(FindingRow)
                if (f := Finding.model_validate(r.payload))
            }
            edges: dict[UUID, UUID] = {}
            for head in decision_heads(tuple(await self.decisions())).values():
                f = findings.get(head.finding_id)
                if (
                    head.outcome == "accepted"
                    and f
                    and f.action == "version"
                    and len(f.concept_ids) == 2
                ):
                    edges[f.concept_ids[0]] = f.concept_ids[1]
            edges[finding.concept_ids[0]] = finding.concept_ids[1]
            for identity in edges:
                resolve_alias(identity, edges)
        if finding.action == "canonical_review":
            if len(finding.version_ids) != 1:
                raise ValueError("Canonical selection requires one evidenced version")
            row = await self.session.get(VersionRow, finding.version_ids[0])
            assert row is not None
            version = ConceptVersion.model_validate(row.payload)
            snapshot = await self.snapshot()
            concept = next(c for c in snapshot.concepts if c.id == version.concept_id)
            if not set(version_evidence(concept, version.id)).intersection(finding.occurrence_ids):
                raise ValueError("Canonical finding must identify the selected version's evidence")
            if version.publication.publication_status in {"withdrawn", "superseded", "deprecated"}:
                raise ValueError("Retired knowledge cannot be selected as current canonical")
            prerelease = bool(version.semantic_version and version.semantic_version.prerelease)
            immature = version.publication.publication_status in {
                "draft",
                "working_draft",
                "proposed",
                "consultation",
                "preview",
                "alpha",
                "beta",
                "release_candidate",
            }
            immature = immature or version.id in snapshot.preview_version_ids
            limited = any(
                getattr(finding.scope, f)
                for f in (
                    "organization",
                    "business_unit",
                    "application",
                    "environment",
                    "jurisdiction",
                )
            )
            if (prerelease or immature) and not (finding.allow_prerelease and limited):
                raise ValueError(
                    "Draft canonical adoption requires explicit limited-scope approval"
                )

    async def _authority(self, finding: Finding, decision: Decision) -> None:
        if decision.outcome not in {"accepted", "reversed", "rejected"}:
            return
        actions = ("approve", "promote") if decision.outcome == "accepted" else ("withdraw",)
        for action in actions:
            event = AuthorityEvent(
                id=stable_id(decision.id, action),
                concept_version_id=finding.version_ids[0],
                action=action,
                actor=decision.actor,
                reason=decision.rationale,
                effective_at=decision.effective_at,
                effective_until=decision.effective_until,
                scope=finding.scope,
                recorded_at=decision.recorded_at,
            )
            await self.session.execute(
                insert(AuthorityRow)
                .values(
                    id=event.id,
                    concept_version_id=event.concept_version_id,
                    action=action,
                    payload=event.model_dump(mode="json"),
                )
                .on_conflict_do_nothing()
            )

    async def deviations(self, as_of: date) -> tuple[Deviation, ...]:
        return ()
