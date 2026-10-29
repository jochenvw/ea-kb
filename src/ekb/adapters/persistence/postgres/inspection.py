from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ekb.adapters.persistence.postgres.models import (
    Base,
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
)
from ekb.domain.inspection import (
    BacklogItem,
    BacklogPage,
    BacklogStatus,
    GraphEdge,
    GraphNode,
    KnowledgeGraph,
    SourceExtraction,
    SourceInspection,
    TableColumn,
    TableInfo,
    TablePage,
)
from ekb.domain.models import ConceptVersion, SourceRecord
from ekb.domain.resolution import Decision, Finding


def preview(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[nested preview omitted]"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if str(key).lower() in {"password", "token", "api_key", "authorization", "secret"}
            else preview(item, depth + 1)
            for key, item in list(value.items())[:80]
        }
    if isinstance(value, list):
        return [preview(item, depth + 1) for item in value[:20]] + (
            ["[additional items omitted]"] if len(value) > 20 else []
        )
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + " [preview truncated]"
    return value


class Inspection:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def tables(self) -> list[TableInfo]:
        tables = sorted(Base.metadata.tables.values(), key=lambda table: table.name)
        counts = (
            await self.session.execute(
                select(
                    *(select(func.count()).select_from(table).scalar_subquery() for table in tables)
                )
            )
        ).one()
        return [
            TableInfo(name=table.name, count=count)
            for table, count in zip(tables, counts, strict=True)
        ]

    async def rows(
        self, name: str, limit: int = 50, *, after: str | None = None, record: str | None = None
    ) -> TablePage:
        table = Base.metadata.tables.get(name)
        if table is None:
            raise ValueError("Unknown EKB table")
        key = next(iter(table.primary_key))
        query = select(table).order_by(key)
        try:
            if record is not None:
                query = query.where(key == key.type.python_type(record))
            elif after is not None:
                query = query.where(key > key.type.python_type(after))
        except (ValueError, TypeError):
            raise ValueError("Invalid record identifier") from None
        rows = list((await self.session.execute(query.limit(limit + 1))).mappings())
        return TablePage(
            name=name,
            columns=[
                TableColumn(
                    name=column.name,
                    type=str(column.type),
                    reference=next((fk.column.table.name for fk in column.foreign_keys), None),
                )
                for column in table.columns
            ],
            rows=json.loads(json.dumps([preview(dict(row)) for row in rows[:limit]], default=str)),
            next_cursor=str(rows[limit - 1][key.name]) if len(rows) > limit else None,
        )

    async def source(
        self, source_id: UUID, limit: int = 50, *, after: UUID | None = None
    ) -> SourceInspection | None:
        source = await self.session.get(SourceRow, source_id)
        if source is None:
            return None
        revisions = select(RevisionRow.id).where(RevisionRow.source_id == source_id)
        segments = select(SegmentRow.id).where(SegmentRow.revision_id.in_(revisions))
        own_versions = (
            select(OccurrenceRow.concept_version_id)
            .where(OccurrenceRow.segment_id.in_(segments))
            .distinct()
        )
        counts = (
            await self.session.execute(
                select(
                    select(func.count()).select_from(revisions.subquery()).scalar_subquery(),
                    select(func.count()).select_from(segments.subquery()).scalar_subquery(),
                    select(func.count()).select_from(own_versions.subquery()).scalar_subquery(),
                    select(func.count())
                    .select_from(OccurrenceRow)
                    .where(OccurrenceRow.segment_id.in_(segments))
                    .scalar_subquery(),
                )
            )
        ).one()
        query = (
            select(VersionRow, func.count(OccurrenceRow.id))
            .join(OccurrenceRow, OccurrenceRow.concept_version_id == VersionRow.id)
            .where(OccurrenceRow.segment_id.in_(segments))
            .group_by(VersionRow.id)
            .order_by(VersionRow.id)
        )
        if after:
            query = query.where(VersionRow.id > after)
        rows = list((await self.session.execute(query.limit(limit + 1))).all())
        return SourceInspection(
            source=SourceRecord.model_validate(source.payload),
            revisions=counts[0],
            segments=counts[1],
            versions=counts[2],
            occurrences=counts[3],
            items=[
                SourceExtraction(
                    concept_id=row.concept_id,
                    version=ConceptVersion.model_validate(row.payload),
                    evidence_count=count,
                )
                for row, count in rows[:limit]
            ],
            next_cursor=rows[limit - 1][0].id if len(rows) > limit else None,
        )

    async def _active(self) -> set[UUID]:
        payload = await self.session.scalar(
            select(ExecutionRow.payload)
            .where(
                ExecutionRow.processor_id == "enrichment",
                ExecutionRow.kind == "stage",
                ExecutionRow.status == "done",
            )
            .order_by(ExecutionRow.started_at.desc(), ExecutionRow.id)
            .limit(1)
        )
        output = payload.get("output") if payload else None
        return {UUID(value) for value in output.get("record_ids", [])} if output else set()

    async def backlog(
        self,
        limit: int = 50,
        *,
        after: UUID | None = None,
        status: BacklogStatus = "all",
        check_id: str | None = None,
    ) -> BacklogPage:
        head = (
            select(DecisionRow.id)
            .where(DecisionRow.finding_id == FindingRow.id)
            .order_by(DecisionRow.recorded_at.desc(), DecisionRow.id.desc())
            .correlate(FindingRow)
            .limit(1)
            .scalar_subquery()
        )
        query = (
            select(FindingRow, DecisionRow)
            .outerjoin(DecisionRow, DecisionRow.id == head)
            .where(
                or_(FindingRow.check_id == "manual", FindingRow.id.in_(await self._active())),
                or_(DecisionRow.id.is_(None), DecisionRow.outcome.in_(("deferred", "reversed"))),
            )
        )
        if status != "all":
            query = query.where(
                DecisionRow.id.is_(None) if status == "pending" else DecisionRow.outcome == status
            )
        if check_id:
            query = query.where(FindingRow.check_id == check_id)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        if after:
            query = query.where(FindingRow.id > after)
        rows = list(
            (await self.session.execute(query.order_by(FindingRow.id).limit(limit + 1))).all()
        )
        return BacklogPage(
            items=[
                BacklogItem(
                    finding=Finding.model_validate(finding.payload),
                    decision=Decision.model_validate(decision.payload) if decision else None,
                )
                for finding, decision in rows[:limit]
            ],
            total=total or 0,
            next_cursor=rows[limit - 1][0].id if len(rows) > limit else None,
        )

    async def graph(self, concept_id: UUID) -> KnowledgeGraph | None:
        if await self.session.get(ConceptRow, concept_id) is None:
            return None
        relations = list(
            await self.session.scalars(
                select(RelationshipRow)
                .where(
                    or_(
                        RelationshipRow.from_id == concept_id,
                        RelationshipRow.to_id == concept_id,
                    )
                )
                .order_by(RelationshipRow.id)
                .limit(13)
            )
        )
        findings = list(
            await self.session.scalars(
                select(FindingRow)
                .where(
                    FindingRow.payload["concept_ids"].contains([str(concept_id)]),
                    or_(
                        FindingRow.check_id == "manual",
                        FindingRow.id.in_(await self._active()),
                    ),
                )
                .order_by(FindingRow.id)
                .limit(13)
            )
        )
        edges = [
            GraphEdge(
                source=row.from_id,
                target=row.to_id,
                kind=row.kind,
                status=row.payload.get("status", "asserted"),
                href=f"/concepts/{concept_id}",
            )
            for row in relations[:12]
        ]
        for row in findings[:12]:
            finding = Finding.model_validate(row.payload)
            for other in finding.concept_ids[1:]:
                if concept_id in (finding.concept_ids[0], other):
                    edges.append(
                        GraphEdge(
                            source=finding.concept_ids[0],
                            target=other,
                            kind=finding.relationship or finding.action,
                            status="proposal",
                            href=f"/review?finding_id={finding.id}",
                        )
                    )
        ids = {concept_id}
        retained = []
        for edge in edges:
            if len(ids | {edge.source, edge.target}) <= 13:
                ids.update((edge.source, edge.target))
                retained.append(edge)
        truncated = len(retained) < len(edges)
        ranked = (
            select(
                VersionRow.id,
                func.row_number()
                .over(
                    partition_by=VersionRow.concept_id,
                    order_by=(
                        VersionRow.payload["recorded_at"].astext.desc(),
                        VersionRow.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(VersionRow.concept_id.in_(ids))
            .subquery()
        )
        versions = await self.session.scalars(
            select(VersionRow)
            .where(VersionRow.id.in_(select(ranked.c.id).where(ranked.c.rank == 1)))
            .order_by(VersionRow.concept_id)
        )
        nodes = [
            GraphNode(
                id=version.concept_id,
                label=version.statement,
                kind=version.payload["candidate"]["kind"],
                href=f"/concepts/{version.concept_id}",
            )
            for version in versions
        ]
        sources = list(
            (
                await self.session.execute(
                    select(SourceRow)
                    .join(RevisionRow, RevisionRow.source_id == SourceRow.id)
                    .join(SegmentRow, SegmentRow.revision_id == RevisionRow.id)
                    .join(OccurrenceRow, OccurrenceRow.segment_id == SegmentRow.id)
                    .join(VersionRow, VersionRow.id == OccurrenceRow.concept_version_id)
                    .where(VersionRow.concept_id == concept_id)
                    .distinct()
                    .order_by(SourceRow.id)
                    .limit(9)
                )
            ).scalars()
        )
        for source in sources[:8]:
            nodes.append(
                GraphNode(
                    id=source.id,
                    label=source.payload["item"]["name"],
                    kind="source",
                    href=f"/sources/{source.id}",
                )
            )
            retained.append(
                GraphEdge(
                    source=source.id,
                    target=concept_id,
                    kind="evidence",
                    status="provenance",
                    href=f"/sources/{source.id}",
                )
            )
        return KnowledgeGraph(
            root=concept_id,
            nodes=nodes,
            edges=retained,
            truncated=len(relations) > 12 or len(findings) > 12 or len(sources) > 8 or truncated,
        )
