from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, model_validator

from ekb.domain.models import ConceptDetail, KnowledgeLevel, Model, Scope


class ControlDisposition(StrEnum):
    REQUIRED = "required"
    EXCEPTION = "exception"


class ArchitectureControl(Model):
    concept_id: UUID
    concept_version_id: UUID | None = None
    disposition: ControlDisposition = ControlDisposition.REQUIRED
    rationale: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    expires_on: date | None = None

    @model_validator(mode="after")
    def valid_disposition(self) -> Self:
        exception_values = (self.rationale, self.approved_by, self.approved_at, self.expires_on)
        if self.disposition is ControlDisposition.REQUIRED and any(
            value is not None for value in exception_values
        ):
            raise ValueError("Required controls cannot carry exception approval fields")
        if self.disposition is ControlDisposition.EXCEPTION:
            if not self.rationale or not self.rationale.strip():
                raise ValueError("Control exceptions require a rationale")
            if not self.approved_by or not self.approved_by.strip() or self.approved_at is None:
                raise ValueError("Control exceptions require an approver and approval timestamp")
            if self.expires_on is not None and self.expires_on < self.approved_at.date():
                raise ValueError("Control exception expiry cannot precede approval")
        return self


class ArchitectureManifest(Model):
    architecture_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    owner: str = Field(min_length=1, max_length=200)
    canonical_source: str = Field(min_length=1, max_length=2000)
    version: str = Field(min_length=1, max_length=100)
    scope: Scope = Field(default_factory=Scope)
    controls: tuple[ArchitectureControl, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_manifest(self) -> Self:
        source = urlsplit(self.canonical_source)
        if not source.scheme or source.username or source.password:
            raise ValueError("Canonical source must be an absolute URI without credentials")
        if len({control.concept_id for control in self.controls}) != len(self.controls):
            raise ValueError("An architecture revision cannot repeat a control")
        return self


class SolutionArchitectureRevision(Model):
    id: UUID
    architecture_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    owner: str = Field(min_length=1, max_length=200)
    canonical_source: str = Field(min_length=1, max_length=2000)
    version: str = Field(min_length=1, max_length=100)
    scope: Scope = Field(default_factory=Scope)
    artifact_key: str = Field(min_length=1)
    artifact_name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=200)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    controls: tuple[ArchitectureControl, ...] = Field(min_length=1)
    recorded_at: datetime

    @model_validator(mode="after")
    def valid_revision(self) -> Self:
        if PurePath(self.artifact_name).name != self.artifact_name:
            raise ValueError("Artifact name must not contain a path")
        if any(control.concept_version_id is None for control in self.controls):
            raise ValueError("Architecture revision controls must pin a concept version")
        ArchitectureManifest(
            architecture_id=self.architecture_id,
            name=self.name,
            description=self.description,
            owner=self.owner,
            canonical_source=self.canonical_source,
            version=self.version,
            scope=self.scope,
            controls=self.controls,
        )
        return self


class SolutionArchitecture(Model):
    id: UUID
    revisions: tuple[SolutionArchitectureRevision, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_history(self) -> Self:
        if any(revision.architecture_id != self.id for revision in self.revisions):
            raise ValueError("Architecture revisions must belong to their aggregate")
        if len({revision.id for revision in self.revisions}) != len(self.revisions):
            raise ValueError("Architecture revision IDs must be unique")
        if len({revision.version for revision in self.revisions}) != len(self.revisions):
            raise ValueError("Architecture version labels must be unique")
        if tuple(sorted(self.revisions, key=lambda item: (item.recorded_at, item.id))) != (
            self.revisions
        ):
            raise ValueError("Architecture revisions must be chronologically ordered")
        return self

    @property
    def latest(self) -> SolutionArchitectureRevision:
        return self.revisions[-1]


class ArchitectureRequirementDetail(Model):
    control: ArchitectureControl
    concept: ConceptDetail


class TraceabilityStep(Model):
    concept_id: UUID
    level: KnowledgeLevel
    statement: str
