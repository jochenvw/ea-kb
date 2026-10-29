from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ekb.adapters.persistence.postgres.benchmarks import Benchmarks
from ekb.adapters.persistence.postgres.inspection import Inspection
from ekb.adapters.persistence.postgres.issues import Issues
from ekb.adapters.persistence.postgres.models import (
    AuthorityRow,
    Base,
    ConceptRow,
    FindingRow,
    IssueRow,
    OccurrenceRow,
    ParseRow,
    PublicationRow,
    RelationshipRow,
    RevisionRow,
    RunRow,
    SegmentResultRow,
    SegmentRow,
    SourceRow,
    TaskRow,
    VersionRow,
    WorkflowLeaseRow,
)
from ekb.adapters.persistence.postgres.workflow import ResolutionStore, Workflow
from ekb.domain.grounding import validate_evidence
from ekb.domain.models import (
    Assertion,
    AuthorityEvent,
    ConceptDetail,
    ConceptSource,
    ConceptVersion,
    InferenceResult,
    KnowledgeLevel,
    Occurrence,
    ProcessingTask,
    Provenance,
    PublicationMetadata,
    Relationship,
    ScanReport,
    Segment,
    SourceRecord,
    SourceRevision,
    Usage,
    WorkspaceCounts,
    fingerprint,
    knowledge_level,
    now,
    stable_id,
)
from ekb.domain.publication import project_authority
from ekb.observability import tracer


async def upsert(session: AsyncSession, table: Any, values: dict[str, Any]) -> None:
    statement = insert(table).values(**values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["id"], set_={key: value for key, value in values.items() if key != "id"}
        )
    )


async def immutable(session: AsyncSession, table: Any, values: dict[str, Any]) -> None:
    await session.execute(insert(table).values(**values).on_conflict_do_nothing())


class Sources:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, source_id: UUID) -> SourceRecord | None:
        row = await self.session.get(SourceRow, source_id)
        if row is None:
            return None
        source = SourceRecord.model_validate(row.payload)
        if source.revision_id:
            source = source.model_copy(
                update={"publication": await self._publication(source.revision_id)}
            )
        return source

    async def list(self) -> list[SourceRecord]:
        rows = await self.session.scalars(select(SourceRow).order_by(SourceRow.location))
        sources = [SourceRecord.model_validate(row.payload) for row in rows]
        revisions = [source.revision_id for source in sources if source.revision_id]
        ranked = (
            select(
                PublicationRow.id,
                func.row_number()
                .over(
                    partition_by=PublicationRow.revision_id,
                    order_by=(PublicationRow.recorded_at.desc(), PublicationRow.id),
                )
                .label("position"),
            )
            .where(PublicationRow.revision_id.in_(revisions))
            .subquery()
        )
        assessments = await self.session.scalars(
            select(PublicationRow)
            .join(ranked, ranked.c.id == PublicationRow.id)
            .where(ranked.c.position == 1)
        )
        publications = {
            row.revision_id: PublicationMetadata.model_validate(row.payload) for row in assessments
        }
        return [
            source.model_copy(
                update={
                    "publication": publications.get(source.revision_id, source.publication)
                    if source.revision_id
                    else source.publication
                }
            )
            for source in sources
        ]

    async def save(self, source: SourceRecord) -> None:
        await upsert(
            self.session,
            SourceRow,
            {
                "id": source.item.id,
                "connector": source.item.connector,
                "location": source.item.location,
                "payload": source.model_dump(mode="json"),
            },
        )

    async def revision(self, revision_id: UUID) -> SourceRevision | None:
        row = await self.session.get(RevisionRow, revision_id)
        if row is None:
            return None
        return SourceRevision.model_validate(row.payload).model_copy(
            update={"publication": await self._publication(revision_id)}
        )

    async def _publication(self, revision_id: UUID) -> PublicationMetadata:
        row = await self.session.scalar(
            select(PublicationRow)
            .where(PublicationRow.revision_id == revision_id)
            .order_by(PublicationRow.recorded_at.desc(), PublicationRow.id)
            .limit(1)
        )
        return PublicationMetadata.model_validate(row.payload) if row else PublicationMetadata()

    async def save_publication(
        self, revision_id: UUID, publication: PublicationMetadata, fingerprint: str
    ) -> None:
        revision = await self.session.get(RevisionRow, revision_id)
        if revision is None:
            raise ValueError("Publication assessment requires a stored source revision")
        for reference in publication.evidence:
            if reference.segment_id:
                segment = await self.session.get(SegmentRow, reference.segment_id)
                if (
                    segment is None
                    or segment.revision_id != revision_id
                    or segment.payload["text"][reference.start : reference.end] != reference.excerpt
                ):
                    raise ValueError("Publication evidence must match the exact stored revision")
            elif reference.field:
                source = await self.session.get(SourceRow, revision.source_id)
                assert source is not None
                item = source.payload["item"]
                actual = (
                    item["name"]
                    if reference.field == "name"
                    else item.get("metadata", {}).get(reference.field)
                )
                if actual != reference.excerpt:
                    raise ValueError("Publication metadata evidence must match the stored source")
        await immutable(
            self.session,
            PublicationRow,
            {
                "id": stable_id(revision_id, fingerprint),
                "revision_id": revision_id,
                "fingerprint": fingerprint,
                "payload": publication.model_dump(mode="json"),
            },
        )

    async def save_revision(self, revision: SourceRevision) -> SourceRevision:
        await immutable(
            self.session,
            RevisionRow,
            {
                "id": revision.id,
                "source_id": revision.source_id,
                "content_hash": revision.content_hash,
                "predecessor_id": revision.predecessor_id,
                "payload": revision.model_dump(mode="json"),
            },
        )
        stored = await self.revision(revision.id)
        assert stored is not None
        return stored

    async def save_segments(
        self, revision_id: UUID, segments: list[Segment], parser_fingerprint: str
    ) -> None:
        for segment in segments:
            await immutable(
                self.session,
                SegmentRow,
                {
                    "id": segment.id,
                    "revision_id": segment.revision_id,
                    "parser_fingerprint": parser_fingerprint,
                    "ordinal": segment.ordinal,
                    "parent_id": segment.parent_id,
                    "payload": segment.model_dump(mode="json"),
                },
            )
        await immutable(
            self.session,
            ParseRow,
            {
                "id": stable_id(revision_id, parser_fingerprint),
                "revision_id": revision_id,
                "parser_fingerprint": parser_fingerprint,
            },
        )

    async def segments(self, revision_id: UUID, parser_fingerprint: str) -> list[Segment] | None:
        if await self.session.get(ParseRow, stable_id(revision_id, parser_fingerprint)) is None:
            return None
        rows = await self.session.scalars(
            select(SegmentRow)
            .where(
                SegmentRow.revision_id == revision_id,
                SegmentRow.parser_fingerprint == parser_fingerprint,
            )
            .order_by(SegmentRow.ordinal)
        )
        return [Segment.model_validate(row.payload) for row in rows]


class Knowledge:
    async def counts(self) -> WorkspaceCounts:
        tables = {
            "sources": SourceRow,
            "revisions": RevisionRow,
            "segments": SegmentRow,
            "concepts": ConceptRow,
            "versions": VersionRow,
            "occurrences": OccurrenceRow,
            "proposals": FindingRow,
            "issues": IssueRow,
        }
        columns = [
            select(func.count()).select_from(table).scalar_subquery().label(name)
            for name, table in tables.items()
        ]
        columns.append(
            select(func.count())
            .select_from(IssueRow)
            .where(IssueRow.status == "open")
            .scalar_subquery()
            .label("open_issues")
        )
        return WorkspaceCounts.model_validate(
            (await self.session.execute(select(*columns))).mappings().one()
        )

    def __init__(self, session: AsyncSession):
        self.session = session

    async def search(
        self,
        query: str = "",
        limit: int = 50,
        *,
        after: UUID | None = None,
        source_id: UUID | None = None,
        level: KnowledgeLevel | None = None,
    ) -> list[ConceptDetail]:
        matches = select(VersionRow.concept_id, VersionRow.id)
        if level is not None:
            explicit_platform = (
                func.length(
                    func.regexp_replace(
                        func.coalesce(
                            VersionRow.payload["candidate"]["scope"]["platform"].astext, ""
                        ),
                        r"\s",
                        "",
                        "g",
                    )
                )
                > 0
            )
            matches = matches.where(
                case(
                    (
                        and_(
                            VersionRow.payload["candidate"]["kind"].astext == "requirement",
                            explicit_platform,
                        ),
                        "implementation",
                    ),
                    else_="top_level",
                )
                == level.value
            )
        if query:
            matches = matches.where(
                or_(
                    VersionRow.statement.icontains(query, autoescape=True),
                    VersionRow.assertion["text"].astext.icontains(query, autoescape=True),
                )
            )
        if source_id is not None:
            own = (
                select(OccurrenceRow.concept_version_id)
                .join(SegmentRow, OccurrenceRow.segment_id == SegmentRow.id)
                .join(RevisionRow, SegmentRow.revision_id == RevisionRow.id)
                .where(RevisionRow.source_id == source_id)
            )
            matches = matches.where(VersionRow.id.in_(own))
        matched = matches.subquery()
        statement = select(matched.c.concept_id).distinct()
        if after is not None:
            statement = statement.where(matched.c.concept_id > after)
        ids = list(
            await self.session.scalars(
                statement.order_by(matched.c.concept_id).limit(min(limit, 200))
            )
        )
        matching_ids = set(
            await self.session.scalars(select(matched.c.id).where(matched.c.concept_id.in_(ids)))
        )
        results = []
        for concept_id in ids:
            detail = await self.get(concept_id)
            if detail:
                version = next(v for v in reversed(detail.versions) if v.id in matching_ids)
                results.append(detail.model_copy(update={"matching_version_id": version.id}))
        return results

    async def save_assertion(self, version_id: UUID, assertion: Assertion) -> None:
        occurrence = await self.session.get(OccurrenceRow, assertion.occurrence_id)
        if occurrence is None or occurrence.concept_version_id != version_id:
            raise ValueError("An assertion requires evidence for the exact version")
        source_id = await self.session.scalar(
            select(RevisionRow.source_id)
            .join(SegmentRow, SegmentRow.revision_id == RevisionRow.id)
            .where(SegmentRow.id == occurrence.segment_id)
        )
        if source_id != assertion.source_id:
            raise ValueError("An assertion must retain its exact source binding")
        await self.session.execute(
            update(VersionRow)
            .where(VersionRow.id == version_id)
            .values(assertion=assertion.model_dump(mode="json"))
        )

    async def get(self, concept_id: UUID) -> ConceptDetail | None:
        if await self.session.get(ConceptRow, concept_id) is None:
            return None
        versions = await self.session.scalars(
            select(VersionRow)
            .where(VersionRow.concept_id == concept_id)
            .order_by(VersionRow.payload["recorded_at"].astext, VersionRow.id)
        )
        version_list = [row.to_domain() for row in versions]
        version_ids = [v.id for v in version_list]
        occurrences = await self.session.scalars(
            select(OccurrenceRow).where(OccurrenceRow.concept_version_id.in_(version_ids))
        )
        relationships = await self.session.scalars(
            select(RelationshipRow).where(
                or_(RelationshipRow.from_id == concept_id, RelationshipRow.to_id == concept_id)
            )
        )
        events = await self.session.scalars(
            select(AuthorityRow).where(AuthorityRow.concept_version_id.in_(version_ids))
        )
        event_list = [AuthorityEvent.model_validate(row.payload) for row in events]
        citations: dict[UUID, ConceptSource] = {}
        source_rows = await self.session.execute(
            select(SourceRow, OccurrenceRow.concept_version_id, func.count(OccurrenceRow.id))
            .join(RevisionRow, RevisionRow.source_id == SourceRow.id)
            .join(SegmentRow, SegmentRow.revision_id == RevisionRow.id)
            .join(OccurrenceRow, OccurrenceRow.segment_id == SegmentRow.id)
            .where(OccurrenceRow.concept_version_id.in_(version_ids))
            .group_by(SourceRow.id, OccurrenceRow.concept_version_id)
            .order_by(SourceRow.id, OccurrenceRow.concept_version_id)
        )
        for source_row, version_id, count in source_rows:
            source = SourceRecord.model_validate(source_row.payload)
            publisher = source.item.metadata.get("publisher")
            if source_row.id not in citations:
                citations[source_row.id] = ConceptSource(
                    id=source_row.id,
                    name=source.item.name,
                    publisher=publisher if isinstance(publisher, str) else None,
                    location=source.item.location,
                    version_ids=[],
                )
            citations[source_row.id].version_ids.append(version_id)
            citations[source_row.id].evidence_counts[version_id] = count
        return ConceptDetail(
            id=concept_id,
            versions=version_list,
            occurrences=[Occurrence.model_validate(row.payload) for row in occurrences],
            relationships=[Relationship.model_validate(row.payload) for row in relationships],
            authority_events=[event.model_dump(mode="json") for event in event_list],
            sources=list(citations.values()),
            levels={version.id: knowledge_level(version.candidate) for version in version_list},
            authority_states=project_authority(
                event_list,
                datetime.now(UTC).date(),
                version_ids,
            ),
        )

    async def persist(self, segment: Segment, result: InferenceResult) -> list[UUID]:
        validate_evidence(result.extraction, segment)
        stored_segment = await self.session.get(SegmentRow, segment.id)
        if stored_segment is None or Segment.model_validate(stored_segment.payload) != segment:
            raise ValueError("Evidence must refer to the exact persisted segment")
        concept_ids: list[UUID] = []
        version_ids: list[UUID] = []
        revision = await Sources(self.session).revision(segment.revision_id)
        assert revision is not None
        publication = revision.publication
        publication_identity = publication.model_dump(mode="json", exclude={"evidence", "notes"})
        for candidate in result.extraction.candidates:
            normalized = re.sub(r"\s+", " ", candidate.statement).strip().casefold()
            identity_key = json.dumps(
                [
                    candidate.kind,
                    normalized,
                    candidate.modality,
                    candidate.scope.model_dump(mode="json"),
                ],
                sort_keys=True,
            )
            concept_id = stable_id("concept", identity_key)
            version = ConceptVersion(
                id=stable_id(
                    concept_id,
                    "unversioned",
                    fingerprint(publication=publication_identity),
                ),
                concept_id=concept_id,
                candidate=candidate,
                publication=publication,
            )
            previous_unversioned = await self.session.scalars(
                select(VersionRow).where(
                    VersionRow.concept_id == concept_id,
                    VersionRow.semantic_version.is_(None),
                )
            )
            for existing in previous_unversioned:
                stored = ConceptVersion.model_validate(existing.payload)
                if (
                    stored.publication.model_dump(mode="json", exclude={"evidence", "notes"})
                    == publication_identity
                ):
                    version = stored
                    break
            with tracer.start_as_current_span(
                "concept.persistence", attributes={"concept.id": str(concept_id)}
            ):
                await immutable(
                    self.session,
                    ConceptRow,
                    {"id": concept_id, "identity_key": identity_key, "kind": candidate.kind},
                )
                await immutable(
                    self.session,
                    VersionRow,
                    {
                        "id": version.id,
                        "concept_id": concept_id,
                        "semantic_version": None,
                        "statement": candidate.statement,
                        "predecessor_id": None,
                        "payload": version.model_dump(mode="json", exclude={"assertion"}),
                    },
                )
                await self._occurrence(version.id, segment, candidate.excerpt, result)
            concept_ids.append(concept_id)
            version_ids.append(version.id)
        for proposal in result.extraction.relationships:
            from_id, to_id = concept_ids[proposal.from_index], concept_ids[proposal.to_index]
            if from_id == to_id:
                continue
            occurrence = await self._occurrence(
                version_ids[proposal.from_index], segment, proposal.excerpt, result
            )
            relationship = Relationship(
                id=stable_id(from_id, to_id, proposal.kind, occurrence.id),
                from_concept_id=from_id,
                to_concept_id=to_id,
                kind=proposal.kind,
                occurrence_id=occurrence.id,
                scope=proposal.scope,
            )
            with tracer.start_as_current_span("relationship.creation"):
                await immutable(
                    self.session,
                    RelationshipRow,
                    {
                        "id": relationship.id,
                        "from_id": from_id,
                        "to_id": to_id,
                        "kind": proposal.kind,
                        "occurrence_id": occurrence.id,
                        "payload": relationship.model_dump(mode="json"),
                    },
                )
        return concept_ids

    async def _occurrence(
        self, version_id: UUID, segment: Segment, excerpt: str, result: InferenceResult
    ) -> Occurrence:
        start = segment.text.index(excerpt)
        end = start + len(excerpt)
        occurrence = Occurrence(
            id=stable_id(version_id, segment.id, start, end),
            concept_version_id=version_id,
            segment_id=segment.id,
            excerpt=excerpt,
            start=start,
            end=end,
            extraction_metadata={
                "model_id": result.model_id,
                "prompt_version": result.prompt_version,
                "usage": result.usage.model_dump(),
                "authority": "candidate",
            },
        )
        await immutable(
            self.session,
            OccurrenceRow,
            {
                "id": occurrence.id,
                "concept_version_id": version_id,
                "segment_id": segment.id,
                "start_offset": start,
                "end_offset": end,
                "payload": occurrence.model_dump(mode="json"),
            },
        )
        return occurrence

    async def provenance(self, concept_id: UUID) -> list[Provenance]:
        rows = await self.session.execute(
            select(OccurrenceRow, SegmentRow, RevisionRow, SourceRow)
            .join(SegmentRow, OccurrenceRow.segment_id == SegmentRow.id)
            .join(RevisionRow, SegmentRow.revision_id == RevisionRow.id)
            .join(SourceRow, RevisionRow.source_id == SourceRow.id)
            .join(VersionRow, OccurrenceRow.concept_version_id == VersionRow.id)
            .where(VersionRow.concept_id == concept_id)
        )
        results = []
        for occ, seg, rev, src in rows:
            neighbors = list(
                await self.session.scalars(
                    select(SegmentRow)
                    .where(
                        SegmentRow.revision_id == seg.revision_id,
                        SegmentRow.parser_fingerprint == seg.parser_fingerprint,
                        SegmentRow.ordinal.in_((seg.ordinal - 1, seg.ordinal + 1)),
                    )
                    .order_by(SegmentRow.ordinal)
                )
            )
            results.append(
                Provenance(
                    occurrence=Occurrence.model_validate(occ.payload),
                    segment=Segment.model_validate(seg.payload),
                    revision=SourceRevision.model_validate(rev.payload),
                    source=SourceRecord.model_validate(src.payload),
                    context_before=next(
                        (
                            Segment.model_validate(row.payload)
                            for row in neighbors
                            if row.ordinal < seg.ordinal
                        ),
                        None,
                    ),
                    context_after=next(
                        (
                            Segment.model_validate(row.payload)
                            for row in neighbors
                            if row.ordinal > seg.ordinal
                        ),
                        None,
                    ),
                )
            )
        return results

    async def similar(self, concept_id: UUID, limit: int = 10) -> list[ConceptDetail]:
        concept = await self.get(concept_id)
        if not concept:
            return []
        statement = concept.versions[-1].candidate.statement.casefold()
        words = re.findall(r"\w{4,}", statement)[:8]
        if not words:
            return []
        rows = await self.session.scalars(
            select(VersionRow)
            .where(
                VersionRow.concept_id != concept_id,
                or_(*(VersionRow.statement.icontains(w, autoescape=True) for w in words)),
            )
            .limit(200)
        )
        ranked = sorted(
            rows,
            key=lambda row: SequenceMatcher(None, statement, row.statement.casefold()).ratio(),
            reverse=True,
        )
        ids = list(dict.fromkeys(row.concept_id for row in ranked))[:limit]
        return [detail for cid in ids if (detail := await self.get(cid))]

    async def record_authority(self, event: AuthorityEvent) -> None:
        await immutable(
            self.session,
            AuthorityRow,
            {
                "id": event.id,
                "concept_version_id": event.concept_version_id,
                "action": event.action,
                "payload": event.model_dump(mode="json"),
            },
        )

    async def add_version(self, version: ConceptVersion) -> None:
        previous = await self.session.get(VersionRow, version.predecessor_id)
        if previous is None or previous.concept_id != version.concept_id:
            raise ValueError("An explicit predecessor in the same concept is required")
        previous_model = ConceptVersion.model_validate(previous.payload)
        if version.semantic_version is None:
            raise ValueError("Explicit semantic versions require an established version")
        if previous_model.semantic_version is not None and (
            version.semantic_version.compare_precedence(previous_model.semantic_version) <= 0
        ):
            raise ValueError("New semantic version must exceed its predecessor")
        await self.session.execute(
            insert(VersionRow).values(
                id=version.id,
                concept_id=version.concept_id,
                semantic_version=str(version.version),
                predecessor_id=version.predecessor_id,
                statement=version.candidate.statement,
                payload=version.model_dump(mode="json", exclude={"assertion"}),
            )
        )


class Lineage:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def trace_origins(self, concept_id: UUID, max_depth: int = 20) -> list[UUID]:
        return await self._traverse(concept_id, False, max_depth)

    async def find_dependents(self, concept_id: UUID, max_depth: int = 20) -> list[UUID]:
        return await self._traverse(concept_id, True, max_depth)

    async def _traverse(self, concept_id: UUID, reverse: bool, max_depth: int) -> list[UUID]:
        start, target = ("to_id", "from_id") if reverse else ("from_id", "to_id")
        # Column names are chosen solely from fixed internal constants, never user input.
        statement = text(f"""
            WITH RECURSIVE lineage(id, path, depth) AS (
                SELECT CAST(:root AS uuid), ARRAY[CAST(:root AS uuid)], 0
                UNION ALL
                SELECT r.{target}, l.path || r.{target}, l.depth + 1
                FROM relationships r JOIN lineage l ON r.{start} = l.id
                WHERE r.kind IN ('DERIVES_FROM','DEPENDS_ON')
                AND NOT r.{target} = ANY(l.path) AND l.depth < :depth
            )
            SELECT DISTINCT id FROM lineage WHERE depth > 0
        """)
        rows = await self.session.scalars(
            statement, {"root": concept_id, "depth": max(1, min(max_depth, 100))}
        )
        return list(rows)


class Processing:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def task(self, revision_id: UUID, fingerprint: str) -> ProcessingTask | None:
        row = await self.session.scalar(
            select(TaskRow).where(
                TaskRow.revision_id == revision_id, TaskRow.fingerprint == fingerprint
            )
        )
        return self._task(row) if row else None

    @staticmethod
    def _task(row: TaskRow) -> ProcessingTask:
        return ProcessingTask.model_validate(row.payload).model_copy(
            update={"status": row.status, "attempts": row.attempts}
        )

    async def claim(self, task: ProcessingTask, owner: UUID) -> bool:
        until = now() + timedelta(minutes=5)
        statement = insert(TaskRow).values(
            id=task.id,
            revision_id=task.revision_id,
            fingerprint=task.fingerprint,
            status="running",
            owner=owner,
            lease_until=until,
            attempts=1,
            payload=task.model_dump(mode="json"),
        )
        result = await self.session.scalar(
            statement.on_conflict_do_update(
                index_elements=["revision_id", "fingerprint"],
                set_={
                    "status": "running",
                    "owner": owner,
                    "lease_until": until,
                    "attempts": TaskRow.attempts + 1,
                },
                where=and_(
                    TaskRow.status != "done",
                    or_(
                        TaskRow.status != "running",
                        TaskRow.lease_until < now(),
                    ),
                ),
            ).returning(TaskRow.id)
        )
        return result is not None

    async def heartbeat(self, task_id: UUID, owner: UUID) -> None:
        result = await self.session.scalar(
            update(TaskRow)
            .where(TaskRow.id == task_id, TaskRow.owner == owner, TaskRow.status == "running")
            .values(lease_until=now() + timedelta(minutes=5))
            .returning(TaskRow.id)
        )
        if result is None:
            raise RuntimeError("Processing lease lost")

    async def finish(self, task: ProcessingTask, owner: UUID) -> None:
        row = await self.session.scalar(
            select(TaskRow)
            .where(TaskRow.id == task.id, TaskRow.owner == owner, TaskRow.status == "running")
            .with_for_update()
        )
        if row is None:
            raise RuntimeError("Cannot complete a task owned by another worker")
        task = task.model_copy(update={"usage": self._task(row).usage})
        result = await self.session.scalar(
            update(TaskRow)
            .where(TaskRow.id == task.id, TaskRow.owner == owner, TaskRow.status == "running")
            .values(
                status=task.status,
                lease_until=None,
                owner=None,
                payload=task.model_dump(mode="json"),
            )
            .returning(TaskRow.id)
        )
        if result is None:
            raise RuntimeError("Cannot complete a task owned by another worker")

    async def accumulate_usage(self, task_id: UUID, owner: UUID, usage: Usage) -> None:
        row = await self.session.scalar(
            select(TaskRow)
            .where(TaskRow.id == task_id, TaskRow.owner == owner, TaskRow.status == "running")
            .with_for_update()
        )
        if row is None:
            raise RuntimeError("Processing lease lost while recording usage")
        task = self._task(row)
        old = task.usage
        costs = [cost for cost in (old.cost, usage.cost) if cost is not None]
        combined = Usage(
            input_tokens=old.input_tokens + usage.input_tokens,
            output_tokens=old.output_tokens + usage.output_tokens,
            latency_ms=old.latency_ms + usage.latency_ms,
            cost=sum(costs) if costs else None,
            validation_failures=old.validation_failures + usage.validation_failures,
        )
        row.payload = task.model_copy(update={"usage": combined}).model_dump(mode="json")

    async def save_scan(self, report: ScanReport) -> None:
        await upsert(
            self.session,
            RunRow,
            {
                "id": report.id,
                "started_at": report.started_at,
                "payload": report.model_dump(mode="json"),
            },
        )

    async def scans(self, limit: int = 25) -> list[ScanReport]:
        rows = await self.session.scalars(
            select(RunRow).order_by(RunRow.started_at.desc()).limit(limit)
        )
        return [ScanReport.model_validate(row.payload) for row in rows]

    async def tasks(self, limit: int = 100) -> list[ProcessingTask]:
        rows = await self.session.scalars(
            select(TaskRow).order_by(TaskRow.id).limit(min(limit, 1000))
        )
        return [self._task(row) for row in rows]

    async def cached(self, segment_id: UUID, fingerprint: str) -> InferenceResult | None:
        row = await self.session.scalar(
            select(SegmentResultRow)
            .where(SegmentResultRow.inference_fingerprint == fingerprint)
            .limit(1)
        )
        return InferenceResult.model_validate(row.payload) if row else None

    async def cache(self, segment_id: UUID, fingerprint: str, result: InferenceResult) -> None:
        await immutable(
            self.session,
            SegmentResultRow,
            {
                "id": stable_id(segment_id, fingerprint),
                "segment_id": segment_id,
                "inference_fingerprint": fingerprint,
                "payload": result.model_dump(mode="json"),
            },
        )


class PostgresUnitOfWork:
    def __init__(self, factory: async_sessionmaker[AsyncSession]):
        self.session = factory()
        self.sources = Sources(self.session)
        self.knowledge = Knowledge(self.session)
        self.lineage = Lineage(self.session)
        self.processing = Processing(self.session)
        self.benchmarks = Benchmarks(self.session)
        self.workflow = Workflow(self.session)
        self.resolution = ResolutionStore(self.session)
        self.issues = Issues(self.session)
        self.inspection = Inspection(self.session)

    async def __aenter__(self) -> PostgresUnitOfWork:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.session.rollback()
        await self.session.close()

    async def commit(self) -> None:
        with tracer.start_as_current_span("persistence.transaction"):
            await self.session.commit()


class Postgres:
    def __init__(self, url: str, *, schema: str | None = None):
        connect_args = {"server_settings": {"search_path": schema}} if schema else {}
        self.engine: AsyncEngine = create_async_engine(
            url, pool_pre_ping=True, connect_args=connect_args
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    def __call__(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(self.sessions)

    @asynccontextmanager
    async def reset(self) -> AsyncIterator[None]:
        """Clear only EKB tables, keeping migrations and unrelated application tables."""
        async with self.engine.begin() as connection:
            names = ", ".join(
                connection.dialect.identifier_preparer.quote(table.name)
                for table in Base.metadata.sorted_tables
            )
            await connection.execute(text(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE NOWAIT"))
            active_lease = await connection.scalar(
                select(WorkflowLeaseRow.name).where(WorkflowLeaseRow.expires_at > now()).limit(1)
            )
            active_scan = await connection.scalar(
                select(RunRow.id).where(RunRow.payload["status"].astext == "running").limit(1)
            )
            active_task = await connection.scalar(
                select(TaskRow.id)
                .where(TaskRow.status == "running", TaskRow.lease_until > now())
                .limit(1)
            )
            if active_lease or active_scan or active_task:
                raise ValueError("Workspace has an active scan or lease; stop it before --fresh")
            for table in reversed(Base.metadata.sorted_tables):
                await connection.execute(table.delete())
            yield

    async def close(self) -> None:
        await self.engine.dispose()
