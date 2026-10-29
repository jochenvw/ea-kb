from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ekb.domain.grounding import GroundingError, validate_evidence
from ekb.domain.models import (
    Candidate,
    ConceptKind,
    Extraction,
    Modality,
    Scope,
    Segment,
    SemanticVersion,
    content_hash,
    fingerprint,
    stable_id,
)


@given(st.binary())
def test_hash_and_identity_are_deterministic(content):
    assert content_hash(content) == content_hash(content)
    assert stable_id(content_hash(content)) == stable_id(content_hash(content))
    assert len(content_hash(content)) == 64


def test_processing_fingerprint_tracks_configuration_not_source_identity():
    assert fingerprint(content="x", parser="1") != fingerprint(content="x", parser="2")
    assert fingerprint(a=1, b=2) == fingerprint(b=2, a=1)


def test_scope_and_modality_validation():
    with pytest.raises(ValidationError):
        Scope.model_validate({"effective_from": "2026-02-01", "effective_to": "2026-01-01"})
    with pytest.raises(ValidationError):
        Candidate(
            kind=ConceptKind.OBJECTIVE,
            statement="Reduce costs",
            excerpt="Reduce costs",
            modality=Modality.MANDATED,
        )
    with pytest.raises(ValidationError):
        Candidate(kind=ConceptKind.REQUIREMENT, statement="x", excerpt="x", confidence=1.1)
    assert (
        Candidate(kind=ConceptKind.REQUIREMENT, statement="x", excerpt="x").modality
        == Modality.UNSPECIFIED
    )


@pytest.mark.parametrize("value", ["1.0", "01.2.3", "-1.0.0", "1.2.3-preview"])
def test_invalid_semver(value):
    with pytest.raises(ValueError):
        SemanticVersion.parse(value)


def test_semver():
    assert str(SemanticVersion.parse("2.12.3")) == "2.12.3"


def test_exact_evidence_grounding_preserves_negation():
    segment = Segment(
        id=uuid4(),
        revision_id=uuid4(),
        ordinal=0,
        text="Operators must not disable alarms except during approved maintenance.",
    )
    valid = Candidate(kind=ConceptKind.REQUIREMENT, statement=segment.text, excerpt=segment.text)
    validate_evidence(Extraction(candidates=[valid]), segment)
    with pytest.raises(GroundingError):
        validate_evidence(
            Extraction(
                candidates=[valid.model_copy(update={"excerpt": "Operators must disable alarms"})]
            ),
            segment,
        )
