import re

from ekb.domain.grounding import GroundingError, validate_evidence
from ekb.domain.models import ContentLabeling, Extraction, Modality, Segment, SemanticType


def validate_labeling(labeling: ContentLabeling, segment: Segment) -> None:
    if labeling.coverage and labeling.coverage.excerpt not in segment.text:
        raise GroundingError("Coverage evidence is not an exact source substring")
    for label in labeling.labels:
        if label.excerpt not in segment.text:
            raise GroundingError("Label evidence is not an exact source substring")
        for value in label.scope.model_dump(mode="json", exclude_none=True).values():
            if str(value) not in label.excerpt and str(value) not in (segment.heading or ""):
                raise GroundingError(
                    "Labeled scope must occur verbatim in its evidence or section heading"
                )
        if label.semantic_type == SemanticType.PLATFORM_REQUIREMENT and not label.scope.platform:
            raise GroundingError("Platform labels require an evidenced platform")
        if (
            label.semantic_type
            in {
                SemanticType.ARCHITECTURE_DOMAIN,
                SemanticType.CONCEPTUAL_REQUIREMENT,
                SemanticType.TECHNICAL_REQUIREMENT,
            }
            and label.scope.platform
        ):
            raise GroundingError("Platform-independent labels cannot assert a platform")


def validate_scoped_extraction(
    extraction: Extraction, segment: Segment, labeling: ContentLabeling
) -> None:
    validate_labeling(labeling, segment)
    validate_evidence(extraction, segment)
    for candidate in extraction.candidates:
        for qualifier in ("not", "no", "never", "without", "only", "unless", "except"):
            if re.search(rf"\b{qualifier}\b", candidate.excerpt, re.I) and not re.search(
                rf"\b{qualifier}\b", candidate.statement, re.I
            ):
                raise GroundingError(f"Extraction dropped the evidenced qualifier {qualifier!r}")
        numbers = set(re.findall(r"\b\d+(?:[.,]\d+)*%?", candidate.excerpt))
        statement_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)*%?", candidate.statement))
        if not numbers <= statement_numbers:
            missing = ", ".join(sorted(numbers - statement_numbers))
            raise GroundingError(f"Extraction dropped numeric qualifier(s): {missing}")
        if statement_numbers - numbers:
            invented = ", ".join(sorted(statement_numbers - numbers))
            raise GroundingError(f"Extraction invented numeric qualifier(s): {invented}")
        if candidate.modality == Modality.MANDATED and not re.search(
            r"\b(must|shall|mandatory|required to)\b", candidate.excerpt, re.I
        ):
            raise GroundingError("Extraction invented a mandatory obligation")
        if not any(
            label.semantic_type
            not in {
                SemanticType.ORGANIZATIONAL_SCOPE,
            }
            and candidate.semantic_type == label.semantic_type
            and candidate.scope == label.scope
            and candidate.excerpt in label.excerpt
            for label in labeling.labels
        ):
            raise GroundingError("Candidate must match an evidenced ontology/scope label")
    for relationship in extraction.relationships:
        if not any(
            relationship.scope == label.scope and relationship.excerpt in label.excerpt
            for label in labeling.labels
        ):
            raise GroundingError("Relationship must remain within labeled evidence and scope")
