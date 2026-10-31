from pathlib import Path
from uuid import uuid4

from ekb.adapters.sources import read_manifest
from ekb.domain.models import (
    Candidate,
    Extraction,
    ProposedRelationship,
    RelationshipKind,
    Scope,
    SemanticType,
    SourceItem,
)
from ekb.pipeline.organization import apply_source_policy


def test_platform_bound_sources_cannot_emit_platform_agnostic_knowledge():
    item = SourceItem(
        id=uuid4(),
        connector="test",
        location="urn:platform",
        name="Platform benchmark",
        format="txt",
        metadata={"platform_scope": "Azure"},
    )
    extraction = Extraction(
        candidates=[
            Candidate(
                statement="Network security",
                excerpt="Network security",
                semantic_type=SemanticType.ARCHITECTURE_DOMAIN,
            ),
            Candidate(
                statement="Segment networks",
                excerpt="Segment networks",
                semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
            ),
            Candidate(
                statement="Use a VPC",
                excerpt="Use a VPC",
                semantic_type=SemanticType.PLATFORM_REQUIREMENT,
                scope=Scope(platform="AWS"),
            ),
        ],
        relationships=[
            ProposedRelationship(
                from_index=1,
                to_index=0,
                kind=RelationshipKind.REFINES,
                excerpt="Segment networks",
            ),
            ProposedRelationship(
                from_index=2,
                to_index=1,
                kind=RelationshipKind.REFINES,
                excerpt="Use a VPC",
            ),
        ],
    )

    result = apply_source_policy(item, extraction)

    assert [candidate.semantic_type for candidate in result.candidates] == [
        SemanticType.PLATFORM_REQUIREMENT,
        SemanticType.PLATFORM_REQUIREMENT,
    ]
    assert [candidate.scope.platform for candidate in result.candidates] == ["Azure", "AWS"]
    assert [
        (relationship.from_index, relationship.to_index) for relationship in result.relationships
    ] == [(1, 0)]
    assert result.relationships[0].scope.platform == "Azure"


def test_public_manifest_defines_ten_complete_cross_layer_principles():
    sources = read_manifest(Path("sources/public.yaml"))
    by_id = {source.id: source for source in sources}
    principles = [principle for source in sources for principle in source.architecture_principles]

    assert len(principles) >= 10
    assert len({principle.id for principle in principles}) == len(principles)
    assert {source.id for source in sources if source.enterprise_requirement} >= {
        "nist-csf-2.0",
        "nist-sp800-53-rev5",
        "iso-iec-27001-metadata",
        "iso-14001-metadata",
    }
    for principle in principles:
        assert by_id[principle.strategic_requirement_source_id].enterprise_requirement
        assert by_id[principle.agnostic.source_id].platform_scope is None
        assert all(by_id[selector.source_id].platform_scope for selector in principle.controls)
