from ekb.domain.models import Extraction, Segment


class GroundingError(ValueError):
    pass


def validate_evidence(extraction: Extraction, segment: Segment) -> None:
    for candidate in extraction.candidates:
        if candidate.excerpt not in segment.text:
            raise GroundingError("Candidate evidence is not an exact source substring")
    for relationship in extraction.relationships:
        if relationship.excerpt not in segment.text:
            raise GroundingError("Relationship evidence is not an exact source substring")
        if max(relationship.from_index, relationship.to_index) >= len(extraction.candidates):
            raise GroundingError("Relationship refers to a nonexistent candidate")
        if relationship.from_index == relationship.to_index:
            raise GroundingError("Self-relationships are not supported")
