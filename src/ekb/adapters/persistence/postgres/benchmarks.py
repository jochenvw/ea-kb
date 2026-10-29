from __future__ import annotations

from sqlalchemy import Text, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ekb.adapters.persistence.postgres.models import (
    BenchmarkDocumentRow,
    OccurrenceRow,
    RevisionRow,
    SegmentRow,
    SourceRow,
    VersionRow,
)
from ekb.domain.benchmarks import BenchmarkRole, StoredBenchmarkDocument


class Benchmarks:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, document: StoredBenchmarkDocument) -> None:
        revision = await self.session.get(RevisionRow, document.revision_id)
        segment = await self.session.get(SegmentRow, document.document.segment_id)
        if (
            revision is None
            or revision.source_id != document.source_id
            or revision.content_hash != document.document.content_hash
            or document.document.asset_id != document.source_id
            or segment is None
            or segment.revision_id != document.revision_id
            or segment.payload["text"] != document.document.text
        ):
            raise ValueError("Benchmark records require the exact persisted source and segment")
        source = await self.session.get(SourceRow, document.source_id)
        assert source is not None
        passages = [
            passage
            for control in document.document.controls
            for passage in (
                control.evidence,
                *([control.principle_evidence] if control.principle_evidence else []),
                *(reference.evidence for reference in control.mappings),
                *(child.evidence for child in control.subcontrols),
            )
        ]
        passages.extend(reference.evidence for reference in document.document.policy_references)
        passages.extend(reference.reference.evidence for reference in document.document.mappings)
        passages.extend(reference.evidence for reference in document.document.successors)
        for passage in passages:
            if (
                passage.source_id != document.source_id
                or passage.source_url != source.location
                or passage.content_hash != revision.content_hash
                or document.document.text[passage.start : passage.end] != passage.excerpt
            ):
                raise ValueError("Benchmark evidence must match the exact stored original")
        if document.concept_ids:
            bound = await self.session.scalars(
                select(VersionRow.concept_id)
                .join(
                    OccurrenceRow,
                    OccurrenceRow.concept_version_id == VersionRow.id,
                )
                .where(OccurrenceRow.segment_id == document.document.segment_id)
            )
            if not set(document.concept_ids.values()).issubset(set(bound)):
                raise ValueError("Benchmark concepts require evidence in the same source segment")
        await self.session.execute(
            insert(BenchmarkDocumentRow)
            .values(
                id=document.id,
                source_id=document.source_id,
                revision_id=document.revision_id,
                segment_id=document.document.segment_id,
                edition=document.edition,
                role=document.role,
                parser_version=document.document.parser_version,
                extractor_version=document.extractor_version,
                recorded_at=document.recorded_at,
                payload=document.model_dump(mode="json"),
            )
            .on_conflict_do_nothing()
        )

    async def documents(
        self,
        edition: str | None = None,
        control_id: str | None = None,
        *,
        role: BenchmarkRole | None = None,
        history: bool = False,
        limit: int = 250,
    ) -> list[StoredBenchmarkDocument]:
        ranked = select(
            BenchmarkDocumentRow.id,
            func.row_number()
            .over(
                partition_by=BenchmarkDocumentRow.source_id,
                order_by=(BenchmarkDocumentRow.recorded_at.desc(), BenchmarkDocumentRow.id),
            )
            .label("position"),
        ).join(SourceRow, SourceRow.id == BenchmarkDocumentRow.source_id)
        if not history:
            ranked = ranked.where(
                SourceRow.payload["revision_id"].astext
                == cast(BenchmarkDocumentRow.revision_id, Text)
            )
        if edition:
            ranked = ranked.where(BenchmarkDocumentRow.edition == edition)
        if role:
            ranked = ranked.where(BenchmarkDocumentRow.role == role)
        ranked_rows = ranked.subquery()
        query = select(BenchmarkDocumentRow).join(
            ranked_rows,
            ranked_rows.c.id == BenchmarkDocumentRow.id,
        )
        if not history:
            query = query.where(ranked_rows.c.position == 1)
        if control_id:
            query = query.where(
                BenchmarkDocumentRow.payload["document"]["controls"].contains(
                    [{"control_id": control_id}]
                )
            )
        rows = await self.session.scalars(
            query.order_by(BenchmarkDocumentRow.recorded_at.desc(), BenchmarkDocumentRow.id).limit(
                max(1, min(limit, 500))
            )
        )
        return [StoredBenchmarkDocument.model_validate(row.payload) for row in rows]
