from uuid import uuid4

from ekb.domain.models import SourceItem, SourceRecord
from ekb.domain.sources import source_groups


def document(edition, role="domain", *, publisher="Example", connector="benchmark"):
    return SourceRecord(
        item=SourceItem(
            id=uuid4(),
            connector=connector,
            location=f"https://example.test/{uuid4()}",
            name=f"{edition} {role}",
            format="md",
            metadata={
                "publisher": publisher,
                "benchmark_edition": edition,
                "benchmark_asset": {"role": role},
            },
        ),
    )


def test_editions_consolidate_documents_without_conflating_retrievals():
    domain = document("example-v1")
    overview = document("example-v1", "overview")
    policy = document("example-v1", "policy")
    second = document("example-v2")
    ordinary = document(None)
    groups = source_groups([domain, second, policy, ordinary, overview])
    assert len(groups) == 3
    first = next(group for group in groups if group.edition == "example-v1")
    assert first.documents == (overview, domain, policy)
    assert next(group for group in groups if group.edition is None).id == ordinary.item.id
    assert first.id == source_groups([policy])[0].id
    assert first.id != next(group.id for group in groups if group.edition == "example-v2")
    assert source_groups(reversed([domain, second, policy, ordinary, overview])) == groups


def test_collection_identity_does_not_cross_publishers_or_connectors():
    groups = source_groups(
        [
            document("example-v1"),
            document("example-v1", publisher="Other publisher"),
            document("example-v1", connector="Other connector"),
        ]
    )
    assert len({group.id for group in groups}) == 3
