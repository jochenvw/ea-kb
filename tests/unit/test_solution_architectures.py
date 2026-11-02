from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ekb.domain.architectures import (
    ArchitectureControl,
    ArchitectureEvidence,
    ArchitectureManifest,
    AssessmentOutcome,
    ControlDisposition,
    SolutionArchitecture,
    SolutionArchitectureRevision,
    coverage_outcome,
)


def exception(*, expires_on: date | None = None) -> ArchitectureControl:
    return ArchitectureControl(
        concept_id=uuid4(),
        disposition=ControlDisposition.EXCEPTION,
        rationale="Public downloads require anonymous network access.",
        approved_by="architecture-board",
        approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_on=expires_on,
    )


def test_manifest_requires_canonical_uri_and_unique_controls():
    concept_id = uuid4()
    with pytest.raises(ValidationError, match="absolute URI"):
        ArchitectureManifest(
            name="Public files",
            description="Publishes approved public downloads.",
            owner="Web team",
            canonical_source="relative/file.pdf",
            version="1",
            controls=(ArchitectureControl(concept_id=concept_id),),
        )
    with pytest.raises(ValidationError, match="cannot repeat"):
        ArchitectureManifest(
            name="Public files",
            description="Publishes approved public downloads.",
            owner="Web team",
            canonical_source="https://example.test/architectures/public-files",
            version="1",
            controls=(
                ArchitectureControl(concept_id=concept_id),
                ArchitectureControl(concept_id=concept_id),
            ),
        )


def test_exception_requires_explicit_approval_and_bounded_dates():
    with pytest.raises(ValidationError, match="rationale"):
        ArchitectureControl(
            concept_id=uuid4(),
            disposition=ControlDisposition.EXCEPTION,
            approved_by="board",
            approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="cannot precede"):
        exception(expires_on=date(2025, 12, 31))
    with pytest.raises(ValidationError, match="cannot carry"):
        ArchitectureControl(concept_id=uuid4(), rationale="Hidden exception")


def test_coverage_distinguishes_evidence_active_exception_and_missing():
    required = ArchitectureControl(concept_id=uuid4())
    evidence = ArchitectureEvidence(
        concept_id=required.concept_id,
        summary="The deployment configures a private endpoint.",
        location="main.bicep:42",
    )
    assert coverage_outcome(required, evidence, date(2026, 1, 2)) is AssessmentOutcome.DECLARED
    active = exception(expires_on=date(2026, 12, 31))
    assert coverage_outcome(active, None, date(2025, 12, 31)) is AssessmentOutcome.MISSING
    assert coverage_outcome(active, None, date(2026, 1, 1)) is AssessmentOutcome.EXCEPTED
    assert coverage_outcome(active, None, date(2026, 1, 2)) is AssessmentOutcome.EXCEPTED
    assert coverage_outcome(active, None, date(2027, 1, 1)) is AssessmentOutcome.MISSING
    assert coverage_outcome(required, None, date(2026, 1, 2)) is AssessmentOutcome.MISSING


def test_architecture_history_is_immutable_and_ordered():
    architecture_id = uuid4()
    revision = SolutionArchitectureRevision(
        id=uuid4(),
        architecture_id=architecture_id,
        name="Public files",
        description="Publishes approved public downloads.",
        owner="Web team",
        canonical_source="https://example.test/architectures/public-files",
        version="1",
        artifact_key="abc",
        artifact_name="architecture.pdf",
        media_type="application/pdf",
        content_hash="a" * 64,
        controls=(ArchitectureControl(concept_id=uuid4(), concept_version_id=uuid4()),),
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    architecture = SolutionArchitecture(id=architecture_id, revisions=(revision,))
    assert architecture.latest is revision
    with pytest.raises(ValidationError, match="belong"):
        SolutionArchitecture(
            id=uuid4(),
            revisions=(revision,),
        )


def test_architecture_revision_requires_pinned_control_versions():
    with pytest.raises(ValidationError, match="pin a concept version"):
        SolutionArchitectureRevision(
            id=uuid4(),
            architecture_id=uuid4(),
            name="Public files",
            description="Publishes approved public downloads.",
            owner="Web team",
            canonical_source="https://example.test/architectures/public-files",
            version="1",
            artifact_key="abc",
            artifact_name="architecture.pdf",
            media_type="application/pdf",
            content_hash="a" * 64,
            controls=(ArchitectureControl(concept_id=uuid4()),),
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
