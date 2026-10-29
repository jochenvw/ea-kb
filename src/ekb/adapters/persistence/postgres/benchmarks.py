from __future__ import annotations

from uuid import UUID

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ekb.adapters.persistence.postgres.models import (
    BenchmarkComparisonRow,
    BenchmarkDocumentRow,
    BenchmarkEmbeddingRow,
    BenchmarkMatchRow,
    OccurrenceRow,
    RevisionRow,
    SegmentRow,
    SourceRow,
    VersionRow,
)
from ekb.domain.benchmarks import BenchmarkRole, NativeControlBinding, StoredBenchmarkDocument
from ekb.domain.reconciliation import ComparisonReport, EmbeddingRecord, Verification


class Benchmarks:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def comparison(self, comparison_id: UUID) -> ComparisonReport | None:
        row = await self.session.get(BenchmarkComparisonRow, comparison_id)
        return ComparisonReport.model_validate(row.payload) if row else None

    async def comparisons(self, edition: str, limit: int = 20) -> list[ComparisonReport]:
        rows = await self.session.scalars(
            select(BenchmarkComparisonRow)
            .where(
                or_(
                    BenchmarkComparisonRow.from_edition == edition,
                    BenchmarkComparisonRow.to_edition == edition,
                )
            )
            .order_by(BenchmarkComparisonRow.recorded_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        return [ComparisonReport.model_validate(row.payload) for row in rows]

    async def embedding(self, embedding_id: UUID) -> EmbeddingRecord | None:
        row = await self.session.get(BenchmarkEmbeddingRow, embedding_id)
        return EmbeddingRecord.model_validate(row.payload) if row else None

    async def save_embedding(self, record: EmbeddingRecord) -> None:
        await self.session.execute(
            insert(BenchmarkEmbeddingRow)
            .values(id=record.id, payload=record.model_dump(mode="json"))
            .on_conflict_do_nothing()
        )

    async def verification(self, key: UUID) -> Verification | None:
        row = await self.session.get(BenchmarkMatchRow, key)
        return Verification.model_validate(row.payload) if row else None

    async def save_verification(self, key: UUID, result: Verification) -> None:
        await self.session.execute(
            insert(BenchmarkMatchRow)
            .values(id=key, payload=result.model_dump(mode="json"))
            .on_conflict_do_nothing()
        )

    async def save_comparison(self, report: ComparisonReport) -> None:
        snapshots = {}
        for snapshot_id in report.source_snapshot_ids:
            row = await self.session.get(BenchmarkDocumentRow, snapshot_id)
            if row is None:
                raise ValueError("Comparison requires persisted source snapshots")
            snapshots[snapshot_id] = StoredBenchmarkDocument.model_validate(row.payload)
        references = [
            reference
            for snapshot in snapshots.values()
            for reference in (
                *(r for c in snapshot.document.controls for r in c.mappings),
                *(m.reference for m in snapshot.document.mappings),
            )
        ]
        policies = [p for s in snapshots.values() for p in s.document.policies]
        policy_references = [
            r
            for s in snapshots.values()
            for r in (
                *s.document.policy_references,
                *(p for c in s.document.controls for p in c.policy_references),
            )
        ]
        native_cache = {}
        for change in report.changes:
            for view in (change.old, change.new):
                if view is None:
                    continue
                snapshot = snapshots.get(view.snapshot_id)
                if snapshot is None or (
                    snapshot.revision_id != view.revision_id
                    or snapshot.document.segment_id != view.segment_id
                    or not any(
                        original.model_dump(exclude={"concept_id"})
                        == view.control.model_dump(exclude={"concept_id"})
                        for original in snapshot.document.controls
                    )
                    or snapshot.concept_ids != view.concept_ids
                ):
                    raise ValueError("Comparison control must match its exact source snapshot")
                for binding in view.mappings:
                    reference = binding.reference
                    fields = {"resolution", "resolved_concept_ids"}
                    if reference.evidence.section != view.control.control_id or not any(
                        original.model_dump(exclude=fields) == reference.model_dump(exclude=fields)
                        for original in references
                    ):
                        raise ValueError("MAPS_TO requires the exact published control reference")
                    key = (reference.framework, reference.revision, reference.control_id)
                    if key not in native_cache:
                        native_cache[key] = (
                            await self.native_controls(
                                reference.framework, reference.revision, reference.control_id
                            )
                            if reference.revision
                            else []
                        )
                    expected_targets = tuple(native_cache[key])
                    resolution = (
                        "resolved"
                        if len(expected_targets) == 1
                        else ("ambiguous" if expected_targets else "unresolved")
                    )
                    expected_ids = (
                        expected_targets[0].concept_ids if len(expected_targets) == 1 else ()
                    )
                    if (
                        binding.native_controls != expected_targets
                        or reference.resolution != resolution
                        or reference.resolved_concept_ids != expected_ids
                    ):
                        raise ValueError(
                            "Native identifiers require exact current catalog bindings"
                        )
                for binding in view.policies:
                    if (
                        binding.reference.evidence.section != view.control.control_id
                        or binding.reference not in policy_references
                        or binding.definition is not None
                        and binding.definition not in policies
                    ):
                        raise ValueError("Policy bindings require actual published source records")
            if change.verification:
                if (
                    change.old is None
                    or change.new is None
                    or change.verification.judgment.old_excerpt
                    not in change.old.control.evidence.excerpt
                    or change.verification.judgment.new_excerpt
                    not in change.new.control.evidence.excerpt
                ):
                    raise ValueError("Semantic evidence must quote both exact source passages")
        for edition in report.editions:
            if edition.snapshot_id:
                source = snapshots.get(edition.snapshot_id)
                if (
                    source is None
                    or source.edition != edition.edition
                    or source.document.successors != edition.successors
                    or source.document.publication != edition.publication
                ):
                    raise ValueError(
                        "Edition maturity and succession require exact source snapshots"
                    )
        await self.session.execute(
            insert(BenchmarkComparisonRow)
            .values(
                id=report.id,
                from_edition=report.from_edition,
                to_edition=report.to_edition,
                recorded_at=report.recorded_at,
                payload=report.model_dump(mode="json"),
            )
            .on_conflict_do_nothing()
        )

    async def native_controls(
        self, framework: str, revision: str, control_id: str
    ) -> list[NativeControlBinding]:
        rows = await self.session.execute(
            select(SourceRow.id, RevisionRow.id, SegmentRow.payload, VersionRow.concept_id)
            .join(RevisionRow, RevisionRow.source_id == SourceRow.id)
            .join(SegmentRow, SegmentRow.revision_id == RevisionRow.id)
            .join(OccurrenceRow, OccurrenceRow.segment_id == SegmentRow.id)
            .join(VersionRow, VersionRow.id == OccurrenceRow.concept_version_id)
            .where(
                SourceRow.payload["item"]["metadata"]["framework_namespace"].astext == framework,
                SourceRow.payload["item"]["metadata"]["framework_revision"].astext == revision,
                SourceRow.payload["revision_id"].astext == cast(RevisionRow.id, Text),
                SegmentRow.payload["location"]["control_labels"].contains([control_id]),
            )
            .limit(2000)
        )
        grouped: dict[tuple, NativeControlBinding] = {}
        for source_id, revision_id, segment, concept_id in rows:
            location = segment["location"]
            key = (source_id, revision_id, location["control_id"])
            existing = grouped.get(key)
            ids = set(existing.concept_ids if existing else ())
            ids.add(concept_id)
            grouped[key] = NativeControlBinding(
                framework=framework,
                revision=revision,
                control_id=location["control_id"],
                labels=tuple(location["control_labels"]),
                source_id=source_id,
                revision_id=revision_id,
                concept_ids=tuple(sorted(ids, key=str)),
            )
        return sorted(
            grouped.values(), key=lambda target: (str(target.source_id), target.control_id)
        )

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
