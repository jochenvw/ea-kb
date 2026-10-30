from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


def now() -> datetime:
    return datetime.now(UTC)


def stable_id(*parts: object) -> UUID:
    return uuid5(NAMESPACE_URL, json.dumps([str(p) for p in parts], separators=(",", ":")))


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fingerprint(**parts: object) -> str:
    return content_hash(json.dumps(parts, sort_keys=True, default=str).encode())


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SemanticType(StrEnum):
    STRATEGIC_GOAL = "StrategicGoal"
    STRATEGIC_REQUIREMENT = "StrategicRequirement"
    ARCHITECTURE_DOMAIN = "ArchitectureDomain"
    CONCEPTUAL_REQUIREMENT = "ConceptualRequirement"
    TECHNICAL_REQUIREMENT = "TechnicalRequirement"
    PLATFORM_REQUIREMENT = "PlatformRequirement"
    ORGANIZATIONAL_SCOPE = "OrganizationalScope"


class KnowledgeLevel(StrEnum):
    TOP_LEVEL = "top_level"
    IMPLEMENTATION = "implementation"


class Modality(StrEnum):
    MANDATED = "mandated"
    RECOMMENDED = "recommended"
    UNSPECIFIED = "unspecified"


class RelationshipKind(StrEnum):
    REFINES = "refines"
    SUPPORTS = "supports"
    CONSTRAINS = "constrains"
    MAPS_TO = "mapsTo"
    DEPENDS_ON = "dependsOn"
    TENSION_WITH = "tensionWith"
    CONFLICTS_WITH = "conflictsWith"


class ClassificationProperty(StrEnum):
    IN_DOMAIN = "inDomain"
    SUB_SCOPE_OF = "subScopeOf"
    APPLIES_TO_SCOPE = "appliesToScope"
    PLATFORM_NAME = "platformName"


REQUIREMENT_TYPES = frozenset(
    {
        SemanticType.STRATEGIC_REQUIREMENT,
        SemanticType.CONCEPTUAL_REQUIREMENT,
        SemanticType.TECHNICAL_REQUIREMENT,
        SemanticType.PLATFORM_REQUIREMENT,
    }
)


class Scope(Model):
    organization: str | None = None
    business_unit: str | None = None
    jurisdiction: str | None = None
    platform: str | None = None
    application: str | None = None
    environment: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("Effective period ends before it starts")
        return self


class SemanticVersion(Model):
    major: int = Field(default=1, ge=0)
    minor: int = Field(default=0, ge=0)
    patch: int = Field(default=0, ge=0)
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_identifiers(self) -> Self:
        for identifier in (*self.prerelease, *self.build):
            if not re.fullmatch(r"[0-9A-Za-z-]+", identifier):
                raise ValueError("SemVer identifiers must contain ASCII letters, digits or hyphens")
        if any(part.isdigit() and len(part) > 1 and part[0] == "0" for part in self.prerelease):
            raise ValueError("Numeric prerelease identifiers cannot have leading zeros")
        return self

    @classmethod
    def parse(cls, value: str) -> SemanticVersion:
        match = re.fullmatch(
            r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
            r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?",
            value,
        )
        if not match:
            raise ValueError("Expected SemVer MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]")
        return cls(
            major=int(match[1]),
            minor=int(match[2]),
            patch=int(match[3]),
            prerelease=tuple(match[4].split(".")) if match[4] else (),
            build=tuple(match[5].split(".")) if match[5] else (),
        )

    def compare_precedence(self, other: SemanticVersion) -> int:
        """SemVer precedence ignores build metadata, without discarding its identity."""
        left, right = (self.major, self.minor, self.patch), (other.major, other.minor, other.patch)
        if left != right:
            return (left > right) - (left < right)
        if not self.prerelease or not other.prerelease:
            return bool(other.prerelease) - bool(self.prerelease)
        for a, b in zip(self.prerelease, other.prerelease, strict=False):
            if a == b:
                continue
            if a.isdigit() and b.isdigit():
                return (int(a) > int(b)) - (int(a) < int(b))
            if a.isdigit() != b.isdigit():
                return -1 if a.isdigit() else 1
            return (a > b) - (a < b)
        return (len(self.prerelease) > len(other.prerelease)) - (
            len(self.prerelease) < len(other.prerelease)
        )

    def __lt__(self, other: SemanticVersion) -> bool:
        return self.compare_precedence(other) < 0

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(self.prerelease)
        if self.build:
            value += "+" + ".".join(self.build)
        return value


class PublicationStatus(StrEnum):
    UNKNOWN = "unknown"
    DRAFT = "draft"
    WORKING_DRAFT = "working_draft"
    PROPOSED = "proposed"
    CONSULTATION = "consultation"
    PREVIEW = "preview"
    ALPHA = "alpha"
    BETA = "beta"
    RELEASE_CANDIDATE = "release_candidate"
    FINAL = "final"
    PUBLISHED = "published"
    APPROVED = "approved"
    DEPRECATED = "deprecated"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"


class GovernanceStatus(StrEnum):
    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class CanonicalStatus(StrEnum):
    NOT_CANONICAL = "not_canonical"
    CANONICAL = "canonical"
    WITHDRAWN = "withdrawn"


class PublicationEvidence(Model):
    excerpt: str = Field(min_length=1)
    field: str | None = None
    segment_id: UUID | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def exact_reference(self) -> Self:
        if self.segment_id is not None:
            if self.start is None or self.end != self.start + len(self.excerpt):
                raise ValueError("Publication evidence must delimit an exact segment excerpt")
        elif not self.field or self.start is not None or self.end is not None:
            raise ValueError("Metadata evidence requires a field; text evidence requires a segment")
        return self


class PublicationMetadata(Model):
    semantic_version: SemanticVersion | None = None
    publisher_version: str | None = None
    publication_status: PublicationStatus = PublicationStatus.UNKNOWN
    evidence: tuple[PublicationEvidence, ...] = ()
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def evidenced_status(self) -> Self:
        if self.publication_status != PublicationStatus.UNKNOWN and not self.evidence:
            raise ValueError("Publication maturity requires source evidence")
        return self


class SourceSpec(Model):
    id: str
    publisher: str
    name: str
    location: str
    format: str
    category: str
    retrieval: str = "http"
    jurisdiction: str | None = None
    access_notes: str
    intended_scope: str
    enabled: bool = True
    metadata_only: bool = False
    control_ids: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)
    publisher_version: str | None = None
    publication_status: PublicationStatus = PublicationStatus.UNKNOWN
    framework_namespace: str | None = None
    framework_revision: str | None = None


class SourceItem(Model):
    id: UUID
    connector: str
    location: str
    name: str
    format: str
    indicator: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def parsing_fingerprint(parser_version: str, item: SourceItem) -> str:
    return fingerprint(
        parser=parser_version,
        format=item.format,
        controls=item.metadata.get("control_ids", []),
        pages=item.metadata.get("pages", []),
    )


class SourceRecord(Model):
    item: SourceItem
    status: str = "available"
    checked_at: datetime = Field(default_factory=now)
    revision_id: UUID | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    publication: PublicationMetadata = Field(default_factory=PublicationMetadata)


class FetchResult(Model):
    content: bytes | None
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


class SourceRevision(Model):
    id: UUID
    source_id: UUID
    content_hash: str
    artifact_key: str
    recorded_at: datetime = Field(default_factory=now)
    predecessor_id: UUID | None = None
    source_location: str | None = None
    metadata_snapshot: dict[str, Any] = Field(default_factory=dict)
    publication: PublicationMetadata = Field(default_factory=PublicationMetadata)


class Segment(Model):
    id: UUID
    revision_id: UUID
    ordinal: int = Field(ge=0)
    text: str
    heading: str | None = None
    parent_id: UUID | None = None
    location: dict[str, Any] = Field(default_factory=dict)
    quality: str = "embedded"
    media: tuple[str, ...] = ()


class Candidate(Model):
    statement: str = Field(min_length=1, max_length=4000)
    excerpt: str = Field(min_length=1)
    modality: Modality = Modality.UNSPECIFIED
    scope: Scope = Field(default_factory=Scope)
    confidence: float = Field(default=0.5, ge=0, le=1)
    source_key: str | None = None
    uncertainty: str | None = None
    semantic_type: SemanticType | None = None

    @model_validator(mode="after")
    def valid_ontology_classification(self) -> Self:
        if (
            self.semantic_type == SemanticType.STRATEGIC_GOAL
            and self.modality != Modality.UNSPECIFIED
        ):
            raise ValueError("Strategic goals do not have requirement modality")
        if self.semantic_type in {
            SemanticType.ARCHITECTURE_DOMAIN,
            SemanticType.ORGANIZATIONAL_SCOPE,
        }:
            raise ValueError("Architecture domains and organizational scopes classify content")
        if self.semantic_type is not None:
            if self.semantic_type == SemanticType.PLATFORM_REQUIREMENT and not self.scope.platform:
                raise ValueError("Platform requirements must identify a platform")
            if (
                self.semantic_type
                in {
                    SemanticType.CONCEPTUAL_REQUIREMENT,
                    SemanticType.TECHNICAL_REQUIREMENT,
                }
                and self.scope.platform
            ):
                raise ValueError("Conceptual and technical requirements are platform independent")
        return self


def knowledge_level(candidate: Candidate) -> KnowledgeLevel:
    if candidate.semantic_type == SemanticType.PLATFORM_REQUIREMENT:
        return KnowledgeLevel.IMPLEMENTATION
    return KnowledgeLevel.TOP_LEVEL


class ProposedRelationship(Model):
    from_index: int = Field(ge=0)
    to_index: int = Field(ge=0)
    kind: RelationshipKind
    excerpt: str = Field(min_length=1)
    scope: Scope = Field(default_factory=Scope)


class Extraction(Model):
    candidates: list[Candidate] = Field(default_factory=list)
    relationships: list[ProposedRelationship] = Field(default_factory=list)


class Usage(Model):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    cost: float | None = Field(default=None, ge=0)
    validation_failures: int = Field(default=0, ge=0)


def total_usage(usages: list[Usage]) -> Usage:
    return Usage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        latency_ms=sum(u.latency_ms for u in usages),
        cost=sum(u.cost for u in usages if u.cost is not None)
        if usages and all(u.cost is not None for u in usages)
        else None,
        validation_failures=sum(u.validation_failures for u in usages),
    )


class ContentLabel(Model):
    semantic_type: SemanticType | None = None
    excerpt: str = Field(min_length=1)
    scope: Scope = Field(default_factory=Scope)
    uncertainty: str | None = None

    @model_validator(mode="after")
    def unknown_is_explicit(self) -> Self:
        if self.semantic_type is None and not self.uncertainty:
            raise ValueError("Unknown ontology layers require an uncertainty explanation")
        return self


class ContentLabeling(Model):
    labels: list[ContentLabel] = Field(default_factory=list, max_length=32)


class LabelingResult(Model):
    labeling: ContentLabeling
    usage: Usage = Field(default_factory=Usage)
    model_id: str
    prompt_version: str


class AssertionDraft(Model):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    uncertainty: str | None = None

    @model_validator(mode="after")
    def standalone_line(self) -> Self:
        if self.text is not None and (
            self.text != self.text.strip() or "\n" in self.text or "\r" in self.text
        ):
            raise ValueError("An assertion must be one trimmed line")
        if self.text is None and not self.uncertainty:
            raise ValueError("Unresolved assertions require an explanation")
        return self


class Assertion(AssertionDraft):
    fingerprint: str
    occurrence_id: UUID
    source_id: UUID
    model_id: str
    prompt_version: str
    usage: Usage = Field(default_factory=Usage)
    recorded_at: datetime = Field(default_factory=now)


class InferenceResult(Model):
    extraction: Extraction
    usage: Usage = Field(default_factory=Usage)
    model_id: str
    prompt_version: str
    labeling: LabelingResult | None = None


class Capabilities(Model):
    model_id: str
    structured_output: bool = False
    embeddings: bool = False
    context_tokens: int = Field(default=8192, gt=0)


class ConceptVersion(Model):
    id: UUID
    concept_id: UUID
    semantic_version: SemanticVersion | None = Field(
        default=None, validation_alias=AliasChoices("semantic_version", "version")
    )
    candidate: Candidate
    assertion: Assertion | None = None
    recorded_at: datetime = Field(default_factory=now)
    predecessor_id: UUID | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    publication: PublicationMetadata = Field(default_factory=PublicationMetadata)

    @property
    def version(self) -> SemanticVersion | None:
        return self.semantic_version


class Occurrence(Model):
    id: UUID
    concept_version_id: UUID
    segment_id: UUID
    excerpt: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.end <= self.start or self.end - self.start != len(self.excerpt):
            raise ValueError("Evidence offsets must delimit the exact excerpt")
        return self


class Relationship(Model):
    id: UUID
    from_concept_id: UUID
    to_concept_id: UUID
    kind: RelationshipKind
    occurrence_id: UUID
    scope: Scope = Field(default_factory=Scope)
    status: str = "proposed"


class ConceptSource(Model):
    id: UUID
    name: str
    publisher: str | None = None
    location: str
    version_ids: list[UUID]
    evidence_counts: dict[UUID, int] = Field(default_factory=dict)


class ConceptDetail(Model):
    id: UUID
    versions: list[ConceptVersion]
    occurrences: list[Occurrence]
    relationships: list[Relationship]
    authority_events: list[dict[str, Any]] = Field(default_factory=list)
    authority_states: list[AuthorityState] = Field(default_factory=list)
    sources: list[ConceptSource] = Field(default_factory=list)
    matching_version_id: UUID | None = None
    levels: dict[UUID, KnowledgeLevel] = Field(default_factory=dict)


class Provenance(Model):
    occurrence: Occurrence
    segment: Segment
    revision: SourceRevision
    source: SourceRecord
    context_before: Segment | None = None
    context_after: Segment | None = None


class ProcessingTask(Model):
    id: UUID
    revision_id: UUID
    fingerprint: str
    status: str = "pending"
    attempts: int = 0
    error: str | None = None
    usage: Usage = Field(default_factory=Usage)


class ScanReport(Model):
    id: UUID
    started_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None
    status: str = "running"
    discovered: int = 0
    known: int = 0
    unknown: int = 0
    downloaded: int = 0
    reused: int = 0
    metadata_only: int = 0
    processed: int = 0
    skipped: int = 0
    missing: int = 0
    failed: int = 0
    concepts: int = 0
    dry_run: bool = False
    errors: list[str] = Field(default_factory=list)


class WorkspaceCounts(Model):
    sources: int = 0
    revisions: int = 0
    segments: int = 0
    concepts: int = 0
    versions: int = 0
    occurrences: int = 0
    proposals: int = 0
    issues: int = 0
    open_issues: int = 0


class AuthorityEvent(Model):
    id: UUID
    concept_version_id: UUID
    action: str = Field(pattern=r"^(review|approve|reject|promote|withdraw)$")
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    recorded_at: datetime = Field(default_factory=now)
    effective_at: date
    effective_until: date | None = None
    scope: Scope = Field(default_factory=Scope)

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if self.effective_until and self.effective_until < self.effective_at:
            raise ValueError("Authority applicability ends before it starts")
        return self


class AuthorityState(Model):
    concept_version_id: UUID
    scope: Scope
    governance_status: GovernanceStatus = GovernanceStatus.PROPOSED
    canonical_status: CanonicalStatus = CanonicalStatus.NOT_CANONICAL
    effective_from: date | None = None
    effective_until: date | None = None
    event_ids: tuple[UUID, ...] = ()


class DeviationReference(Model):
    """Reserved contract; no exception-management workflow is implemented."""

    id: UUID
    concept_version_id: UUID
    scope: Scope
    external_reference: str
