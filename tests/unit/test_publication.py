from datetime import date
from uuid import uuid4

import pytest

from ekb.domain.models import (
    AuthorityEvent,
    CanonicalStatus,
    GovernanceStatus,
    PublicationEvidence,
    PublicationMetadata,
    PublicationStatus,
    Scope,
    Segment,
    SourceItem,
)
from ekb.domain.publication import assess_publication, project_authority


@pytest.mark.parametrize(
    ("text", "status", "version"),
    [
        ("# Microsoft benchmark v2 (preview)", PublicationStatus.PREVIEW, None),
        (
            "# Synthetic enterprise architecture guidelines\nStatus: Draft\nVersion: 2.0.0-draft.1",
            PublicationStatus.DRAFT,
            "2.0.0-draft.1",
        ),
        (
            "# Synthetic external regulation\nStatus: Consultation\nVersion: 2026",
            PublicationStatus.CONSULTATION,
            None,
        ),
        (
            "# Synthetic internal standard\nStatus: Approved\nVersion: 1.0.0",
            PublicationStatus.APPROVED,
            "1.0.0",
        ),
        ("# Synthetic draft standard\nStatus: Withdrawn", PublicationStatus.WITHDRAWN, None),
        (
            "# Control guidance\nTeams must use approved software; preview features may be risky.",
            PublicationStatus.UNKNOWN,
            None,
        ),
    ],
)
def test_source_independent_maturity_has_exact_evidence(text, status, version):
    item = SourceItem(
        id=uuid4(), connector="test", location="urn:synthetic", name="standard.txt", format="txt"
    )
    segment = Segment(id=uuid4(), revision_id=uuid4(), ordinal=0, text=text)
    publication = assess_publication(item, [segment])
    assert publication.publication_status == status
    assert (str(publication.semantic_version) if publication.semantic_version else None) == version
    for reference in publication.evidence:
        assert reference.segment_id == segment.id
        assert text[reference.start : reference.end] == reference.excerpt
    if status != PublicationStatus.UNKNOWN:
        assert publication.evidence


def test_unknown_version_and_conflicting_publication_signals_are_not_fabricated():
    item = SourceItem(
        id=uuid4(),
        connector="test",
        location="urn:synthetic",
        name="standard.pdf",
        format="pdf",
        metadata={"publisher_version": "v2", "publication_status": "draft", "title": "Final"},
    )
    publication = assess_publication(item, [])
    assert publication.publisher_version == "v2"
    assert publication.semantic_version is None
    assert publication.publication_status == PublicationStatus.UNKNOWN
    assert publication.notes
    with pytest.raises(ValueError):
        PublicationMetadata(publication_status=PublicationStatus.PREVIEW)
    with pytest.raises(ValueError):
        PublicationEvidence(excerpt="Draft", segment_id=uuid4(), start=0, end=4)


def test_scoped_draft_approval_does_not_promote_or_approve_other_branches():
    version_id, another_branch = uuid4(), uuid4()
    scope = Scope(business_unit="Synthetic pilot", environment="test")
    approved = AuthorityEvent(
        id=uuid4(),
        concept_version_id=version_id,
        action="approve",
        actor="test-reviewer",
        reason="Limited draft pilot",
        effective_at=date(2026, 1, 1),
        effective_until=date(2026, 2, 1),
        scope=scope,
    )
    future = approved.model_copy(
        update={
            "id": uuid4(),
            "concept_version_id": another_branch,
            "action": "promote",
            "effective_at": date(2026, 3, 1),
            "effective_until": None,
        }
    )
    states = project_authority([approved, future], date(2026, 1, 10))
    assert len(states) == 1
    assert states[0].scope == scope
    assert states[0].governance_status == GovernanceStatus.APPROVED
    assert states[0].canonical_status == CanonicalStatus.NOT_CANONICAL
    assert project_authority([approved], date(2026, 2, 2)) == []
