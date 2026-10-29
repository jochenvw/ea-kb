from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from ekb.domain.models import Model, PublicationMetadata, now

type BenchmarkRole = Literal[
    "overview", "domain", "mapping", "workbook", "repository_index", "policy_index", "policy"
]


class BenchmarkEdition(Model):
    key: str
    family: str
    publisher_version: str
    overview_url: str
    directory: str


class BenchmarkAsset(Model):
    id: UUID
    edition: str
    url: str
    download_url: str
    role: BenchmarkRole
    format: Literal["md", "html", "xlsx", "json"]
    domain: str | None = None
    sha256: str | None = None
    artifact_key: str | None = None
    raw_key: str | None = None
    normalized_key: str | None = None
    normalized_sha256: str | None = None
    definition_id: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    retrieved_at: datetime | None = None
    checked_at: datetime | None = None
    parser_version: str | None = None


class BenchmarkPassage(Model):
    source_id: UUID
    source_url: str
    content_hash: str
    section: str
    excerpt: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def exact_offsets(self) -> BenchmarkPassage:
        if self.end != self.start + len(self.excerpt):
            raise ValueError("Benchmark passage offsets must delimit the exact excerpt")
        return self


class FrameworkReference(Model):
    framework: str
    revision: str | None
    control_id: str
    publisher_label: str
    evidence: BenchmarkPassage
    coverage: Literal["full", "partial", "unknown"] = "unknown"
    resolved_concept_ids: tuple[UUID, ...] = ()
    resolution: Literal["unresolved", "resolved", "ambiguous"] = "unresolved"


class PolicyReference(Model):
    definition_id: str
    name: str
    reference_version: str | None = None
    url: str
    evidence: BenchmarkPassage


class PublishedBenchmarkMapping(Model):
    edition: str
    control_id: str
    reference: FrameworkReference


class BenchmarkSubcontrol(Model):
    control_id: str
    title: str
    text: str
    evidence: BenchmarkPassage


class BenchmarkControl(Model):
    benchmark: str
    edition: str
    publisher_version: str
    domain: str
    control_id: str
    title: str
    security_principle: str | None = None
    generic_guidance: str | None = None
    risk_to_mitigate: str | None = None
    azure_guidance: str = ""
    criticality: str | None = None
    subcontrols: tuple[BenchmarkSubcontrol, ...] = ()
    mappings: tuple[FrameworkReference, ...] = ()
    policy_references: tuple[PolicyReference, ...] = ()
    implementation_options: tuple[str, ...] = ()
    implementation_options_are_alternatives: bool | None = None
    evidence: BenchmarkPassage
    principle_evidence: BenchmarkPassage | None = None
    publication: PublicationMetadata = Field(default_factory=PublicationMetadata)
    concept_id: UUID | None = None


class BenchmarkSuccessor(Model):
    successor: str
    predecessor: str
    evidence: BenchmarkPassage
    status: Literal["published"] = "published"


class PolicyDefinition(Model):
    definition_id: str
    name: str
    version: str | None = None
    resource_types: tuple[str, ...] = ()
    mode: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    rule: dict[str, Any] = Field(default_factory=dict)
    source_url: str
    content_hash: str


class NormalizedBenchmarkDocument(Model):
    asset_id: UUID
    segment_id: UUID
    title: str
    content_hash: str
    parser_version: str
    text: str
    controls: tuple[BenchmarkControl, ...] = ()
    successors: tuple[BenchmarkSuccessor, ...] = ()
    policies: tuple[PolicyDefinition, ...] = ()
    policy_references: tuple[PolicyReference, ...] = ()
    mappings: tuple[PublishedBenchmarkMapping, ...] = ()
    publication: PublicationMetadata = Field(default_factory=PublicationMetadata)
    notes: tuple[str, ...] = ()


class BenchmarkManifest(Model):
    format_version: int = 1
    updated_at: datetime = Field(default_factory=now)
    assets: dict[str, BenchmarkAsset] = Field(default_factory=dict)
    failures: dict[str, str] = Field(default_factory=dict)
    notes: tuple[str, ...] = ()


class BenchmarkSyncReport(Model):
    discovered: int = 0
    downloaded: int = 0
    skipped: int = 0
    reparsed: int = 0
    failed: int = 0
    controls: dict[str, int] = Field(default_factory=dict)
    mappings: int = 0
    policies: int = 0
    failures: dict[str, str] = Field(default_factory=dict)
    notes: tuple[str, ...] = ()
