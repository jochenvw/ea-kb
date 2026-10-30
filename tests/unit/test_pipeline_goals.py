from uuid import uuid4

import pytest

from ekb.domain.models import (
    Candidate,
    Extraction,
    GoalLevel,
    ProposedRelationship,
    RelationshipKind,
    Segment,
    SemanticType,
)
from ekb.pipeline import (
    ProcessingIncompleteError,
    _defer_enterprise_goals,
    _goal_synthesis_chunks,
    _goal_synthesis_text,
)


def candidate(
    statement: str,
    *,
    semantic_type: SemanticType | None = SemanticType.STRATEGIC_GOAL,
    goal_level: GoalLevel | None = GoalLevel.ENTERPRISE,
) -> Candidate:
    return Candidate(
        statement=statement,
        excerpt=statement,
        semantic_type=semantic_type,
        goal_level=goal_level,
    )


def segment(text: str) -> Segment:
    return Segment(id=uuid4(), revision_id=uuid4(), ordinal=0, text=text, heading="Strategy")


def test_enterprise_goal_deferral_keeps_lower_levels_and_remaps_relationships():
    enterprise = candidate("Become a resilient enterprise.")
    target = candidate("Grow sales by 5%.", goal_level=GoalLevel.SUBORDINATE_TARGET)
    requirement = candidate(
        "Operators must retain logs.",
        semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
        goal_level=None,
    )
    extraction = Extraction(
        candidates=[enterprise, target, requirement],
        relationships=[
            ProposedRelationship(
                from_index=1,
                to_index=2,
                kind=RelationshipKind.SUPPORTS,
                excerpt=target.excerpt,
            ),
            ProposedRelationship(
                from_index=0,
                to_index=1,
                kind=RelationshipKind.SUPPORTS,
                excerpt=enterprise.excerpt,
            ),
        ],
    )

    retained, deferred = _defer_enterprise_goals(extraction)

    assert deferred == [enterprise]
    assert retained.candidates == [target, requirement]
    assert [(item.from_index, item.to_index) for item in retained.relationships] == [(0, 1)]


def test_goal_synthesis_input_preserves_original_segment_identity_and_evidence():
    source = segment("We aim to become a resilient enterprise.")
    records = [(source, candidate(source.text))]

    text = _goal_synthesis_text(records)

    assert f"Source key: segment:{source.id}" in text
    assert f"Evidence: {source.text}" in text
    assert _goal_synthesis_chunks(records) == [records]


def test_goal_synthesis_rejects_one_unbounded_candidate():
    source = segment("x" * 100)
    records = [(source, candidate(source.text))]

    with pytest.raises(ProcessingIncompleteError, match="exceeds"):
        _goal_synthesis_chunks(records, max_chars=50)
