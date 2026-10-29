from uuid import UUID

from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from ekb.adapters.persistence.postgres.benchmarks import Benchmarks
from ekb.adapters.persistence.postgres.models import (
    BenchmarkDocumentRow,
    OccurrenceRow,
    RevisionRow,
    SegmentRow,
    SourceRow,
    VersionRow,
)
from ekb.domain.benchmarks import StoredBenchmarkDocument
from ekb.domain.inspection import GraphEdge, GraphNode
from ekb.domain.models import KnowledgeLevel, stable_id


async def implementation_context(
    session: AsyncSession,
    concept_id: UUID,
) -> tuple[list[GraphNode], list[GraphEdge], bool]:
    own_segments = (
        select(OccurrenceRow.segment_id).join(VersionRow).where(VersionRow.concept_id == concept_id)
    )
    native_rows = list(
        (
            await session.execute(
                select(SourceRow.payload, SegmentRow.payload)
                .select_from(SourceRow)
                .join(RevisionRow)
                .join(SegmentRow)
                .where(
                    SegmentRow.id.in_(own_segments),
                    SourceRow.payload["revision_id"].astext == cast(RevisionRow.id, Text),
                )
                .limit(201)
            )
        ).all()
    )
    native_keys: set[tuple[str, str, str]] = set()
    for source, segment in native_rows[:200]:
        metadata = source["item"].get("metadata", {})
        framework, revision = (
            metadata.get("framework_namespace"),
            metadata.get("framework_revision"),
        )
        if isinstance(framework, str) and isinstance(revision, str):
            native_keys.update(
                (framework, revision, label)
                for label in segment.get("location", {}).get("control_labels", [])
                if isinstance(label, str)
            )
    predicates: list[ColumnElement[bool]] = [BenchmarkDocumentRow.segment_id.in_(own_segments)]
    for framework, revision, label in sorted(native_keys)[:32]:
        reference = {"framework": framework, "revision": revision, "control_id": label}
        predicates.extend(
            (
                BenchmarkDocumentRow.payload["document"]["controls"].contains(
                    [{"mappings": [reference]}]
                ),
                BenchmarkDocumentRow.payload["document"]["mappings"].contains(
                    [{"reference": reference}]
                ),
            )
        )
    current = (
        select(
            BenchmarkDocumentRow.id,
            func.row_number()
            .over(
                partition_by=BenchmarkDocumentRow.source_id,
                order_by=(BenchmarkDocumentRow.recorded_at.desc(), BenchmarkDocumentRow.id),
            )
            .label("position"),
        )
        .join(SourceRow, SourceRow.id == BenchmarkDocumentRow.source_id)
        .where(
            SourceRow.payload["revision_id"].astext == cast(BenchmarkDocumentRow.revision_id, Text),
            BenchmarkDocumentRow.role.in_(("domain", "workbook", "mapping")),
        )
        .subquery()
    )
    rows = list(
        await session.scalars(
            select(BenchmarkDocumentRow)
            .join(current, current.c.id == BenchmarkDocumentRow.id)
            .where(current.c.position == 1, or_(*predicates))
            .order_by(BenchmarkDocumentRow.id)
            .limit(21)
        )
    )
    documents = [StoredBenchmarkDocument.model_validate(row.payload) for row in rows[:20]]
    store = Benchmarks(session)
    keys: set[tuple[str, str]] = set()
    for document in documents:
        for control in document.document.controls:
            own = any(
                document.concept_ids.get(key) == concept_id
                for key in (
                    control.control_id,
                    *(child.control_id for child in control.subcontrols),
                )
            )
            matches = any(
                (r.framework, r.revision, r.control_id) in native_keys for r in control.mappings
            )
            if own or matches:
                keys.add((document.edition, control.control_id))
        for mapping in document.document.mappings:
            r = mapping.reference
            if (r.framework, r.revision, r.control_id) in native_keys:
                keys.add((document.edition, mapping.control_id))
    nodes: dict[UUID, GraphNode] = {}
    edges: list[GraphEdge] = []
    seen: set[tuple[UUID, UUID, str]] = set()
    truncated = len(rows) > 20 or len(native_rows) > 200 or len(keys) > 12 or len(native_keys) > 32

    def retain(edge: GraphEdge) -> None:
        key = (edge.source, edge.target, edge.kind)
        if key not in seen:
            seen.add(key)
            edges.append(edge)

    selected_keys = sorted(keys)[:12]
    if not selected_keys:
        return [], [], truncated
    catalog_predicates = [
        and_(
            BenchmarkDocumentRow.edition == edition,
            BenchmarkDocumentRow.payload["document"]["controls"].contains(
                [{"control_id": control_id}]
            ),
        )
        for edition, control_id in selected_keys
    ]
    catalog_predicates.append(
        and_(
            BenchmarkDocumentRow.edition.in_({edition for edition, _ in selected_keys}),
            BenchmarkDocumentRow.role == "mapping",
        )
    )
    catalog_rows = list(
        await session.scalars(
            select(BenchmarkDocumentRow)
            .join(current, current.c.id == BenchmarkDocumentRow.id)
            .where(current.c.position == 1, or_(*catalog_predicates))
            .order_by(BenchmarkDocumentRow.id)
            .limit(41)
        )
    )
    truncated |= len(catalog_rows) > 40
    catalog = [StoredBenchmarkDocument.model_validate(row.payload) for row in catalog_rows[:40]]
    native_cache = {}
    for edition, control_id in selected_keys:
        candidates = [
            d
            for d in catalog
            if d.edition == edition and any(c.control_id == control_id for c in d.document.controls)
        ]
        base = next(
            (
                d
                for d in sorted(candidates, key=lambda d: (d.role != "domain", str(d.id)))
                if d.concept_ids.get(control_id)
            ),
            None,
        )
        if base is None:
            continue
        control = next(c for c in base.document.controls if c.control_id == control_id)
        parent = base.concept_ids[control_id]
        own = parent == concept_id
        href = f"/benchmarks?reference={edition}:{control_id}"
        for child in control.subcontrols:
            if base.concept_ids.get(child.control_id) == concept_id:
                own = True
                retain(
                    GraphEdge(
                        source=concept_id,
                        target=parent,
                        kind="implementation_detail",
                        status="published",
                        href=href,
                        evidence=child.evidence,
                        note="A published subcontrol, not an additional top-level goal.",
                    )
                )
        references = [
            *control.mappings,
            *(
                m.reference
                for d in catalog
                if d.edition == edition and d.role == "mapping"
                for m in d.document.mappings
                if m.control_id == control_id
            ),
        ]
        truncated |= len(references) > 40
        for reference in references[:40]:
            key = (reference.framework, reference.revision, reference.control_id)
            if reference.revision is None or (not own and key not in native_keys):
                continue
            if key not in native_cache:
                native_cache[key] = await store.native_controls(
                    reference.framework, reference.revision, reference.control_id
                )
            targets = native_cache[key]
            if len(targets) != 1:
                continue
            target = targets[0]
            node_id = stable_id(
                "framework-control", target.source_id, target.revision_id, target.control_id
            )
            nodes[node_id] = GraphNode(
                id=node_id,
                kind="framework_control",
                level=KnowledgeLevel.TOP_LEVEL,
                label=f"{reference.publisher_label} / {reference.control_id}",
                href=f"/sources/{target.source_id}",
            )
            if concept_id in target.concept_ids:
                retain(
                    GraphEdge(
                        source=concept_id,
                        target=node_id,
                        kind="part_of_control",
                        status="provenance",
                        href=f"/sources/{target.source_id}",
                        note="Native control membership, not proof of individual clause coverage.",
                    )
                )
            retain(
                GraphEdge(
                    source=parent,
                    target=node_id,
                    kind="MAPS_TO",
                    status="published",
                    href=href,
                    evidence=reference.evidence,
                    note=(
                        "Published control-level mapping, not equivalence, clause coverage, "
                        "compliance or company alignment."
                    ),
                )
            )
    return list(nodes.values()), edges, truncated
