from collections import defaultdict
from collections.abc import Iterable
from uuid import UUID

from ekb.domain.models import Model, SourceRecord, stable_id


class SourceGroup(Model):
    id: UUID
    name: str
    publisher: str | None
    edition: str | None
    documents: tuple[SourceRecord, ...]


def source_groups(records: Iterable[SourceRecord]) -> list[SourceGroup]:
    """Project logical editions without changing physical retrieval identities."""
    members: dict[UUID, list[SourceRecord]] = defaultdict(list)
    editions: dict[UUID, str | None] = {}
    for record in records:
        metadata = record.item.metadata
        value = metadata.get("benchmark_edition")
        edition = value if isinstance(value, str) and value.strip() else None
        group_id = (
            stable_id(
                "source-group",
                record.item.connector,
                metadata.get("publisher"),
                edition,
            )
            if edition
            else record.item.id
        )
        members[group_id].append(record)
        editions[group_id] = edition

    groups: list[SourceGroup] = []
    for group_id, documents in members.items():
        documents.sort(
            key=lambda record: (
                not (
                    isinstance(record.item.metadata.get("benchmark_asset"), dict)
                    and record.item.metadata["benchmark_asset"].get("role") == "overview"
                ),
                record.item.name.casefold(),
                str(record.item.id),
            )
        )
        first = documents[0]
        edition = editions[group_id]
        name = first.item.name
        if edition:
            family, _, version = edition.rpartition("-")
            title = {
                "mcsb": "Microsoft cloud security benchmark",
                "asb": "Azure security benchmark",
            }.get(family)
            name = f"{title} {version}" if title else edition
        publisher = first.item.metadata.get("publisher")
        groups.append(
            SourceGroup(
                id=group_id,
                name=name,
                publisher=publisher if isinstance(publisher, str) else None,
                edition=edition,
                documents=tuple(documents),
            )
        )
    return sorted(groups, key=lambda group: (group.name.casefold(), str(group.id)))
