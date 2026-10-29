from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field

from ekb.domain.models import Model, now


class CheckOptions(Model):
    enabled: bool = True
    similarity_threshold: float = Field(default=0.85, ge=0, le=1)
    threshold: int = Field(default=10, ge=0)
    max_candidates: int = Field(default=500, ge=1, le=10000)
    auto_accept_exact: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)


class StageOptions(Model):
    enabled: bool = True
    checks: dict[str, CheckOptions] = Field(default_factory=dict)
    directory: str | None = None
    manifest: str | None = None


class PipelineConfiguration(Model):
    stages: dict[str, StageOptions] = Field(default_factory=dict)
    retries: int = Field(default=1, ge=0, le=3)


class ProcessorSpec(Model):
    id: str
    name: str
    version: str
    dependencies: tuple[str, ...] = ()
    input_contract: str = "knowledge/v1"
    output_contract: str = "findings/v1"
    capabilities: tuple[str, ...] = ()
    configuration_schema: dict[str, Any] = Field(default_factory=CheckOptions.model_json_schema)
    evidence_required: bool = True


class ArtifactReference(Model):
    contract: str
    revision: str
    producer_fingerprint: str
    source_revision: str
    record_ids: tuple[UUID, ...] = ()


class Execution(Model):
    id: UUID = Field(default_factory=uuid4)
    parent_id: UUID | None = None
    owner: UUID | None = None
    kind: Literal["pipeline", "stage", "check"]
    processor_id: str
    version: str
    fingerprint: str
    status: Literal["running", "done", "failed", "cancelled", "skipped"] = "running"
    started_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None
    attempts: int = 1
    configuration: PipelineConfiguration | None = None
    selected_stages: tuple[str, ...] = ()
    input_revision: str = ""
    output: ArtifactReference | None = None
    error: str | None = None
    statistics: dict[str, int | float] = Field(default_factory=dict)


class PipelineStatus(Model):
    run: Execution
    executions: tuple[Execution, ...] = ()
