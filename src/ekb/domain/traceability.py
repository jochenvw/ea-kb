from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from ekb.domain.models import Model, Scope, SemanticType, Usage


class TraceabilityAssertion(Model):
    concept_id: UUID
    version_id: UUID
    occurrence_id: UUID
    statement: str = Field(min_length=1, max_length=4000)
    excerpt: str = Field(min_length=1)
    semantic_type: SemanticType
    scope: Scope = Field(default_factory=Scope)


class ParentSelection(Model):
    parent_concept_id: UUID
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=4000)
    child_excerpt: str = Field(min_length=1)
    parent_excerpt: str = Field(min_length=1)


class TraceabilitySelection(Model):
    links: tuple[ParentSelection, ...] = Field(default=(), max_length=3)
    unresolved_reason: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def unique_parent_selections(self) -> Self:
        parent_ids = [link.parent_concept_id for link in self.links]
        if len(parent_ids) != len(set(parent_ids)):
            raise ValueError("Traceability selections require unique parent concepts")
        if not self.links and not self.unresolved_reason:
            raise ValueError("An empty selection requires an unresolved reason")
        return self


class TraceabilityVerification(Model):
    selection: TraceabilitySelection
    status: Literal["completed", "failed", "context_limit"] = "completed"
    method: str
    model_id: str | None = None
    model_calls: int = Field(default=0, ge=0)
    usage: Usage = Field(default_factory=Usage)

    @property
    def completed(self) -> bool:
        return self.status == "completed"
