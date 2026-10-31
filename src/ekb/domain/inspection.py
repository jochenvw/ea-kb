from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import JsonValue

from ekb.domain.models import ConceptVersion, KnowledgeLevel, Model, Occurrence, SourceRecord
from ekb.domain.resolution import Decision, Finding


class SourceExtraction(Model):
    concept_id: UUID
    version: ConceptVersion
    evidence_count: int
    level: KnowledgeLevel = KnowledgeLevel.UNCLASSIFIED


class SourceInspection(Model):
    source: SourceRecord
    revisions: int
    segments: int
    versions: int
    occurrences: int
    items: list[SourceExtraction]
    next_cursor: UUID | None


class GraphNode(Model):
    id: UUID
    label: str
    kind: str
    href: str
    level: KnowledgeLevel | None = None


class GraphEdge(Model):
    source: UUID
    target: UUID
    kind: str
    status: str
    href: str
    evidence: Occurrence | None = None
    note: str | None = None


class KnowledgeGraph(Model):
    root: UUID
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool


class BacklogItem(Model):
    finding: Finding
    decision: Decision | None


class BacklogPage(Model):
    items: list[BacklogItem]
    total: int
    next_cursor: UUID | None


type BacklogStatus = Literal["all", "pending", "deferred", "reversed"]


class TableColumn(Model):
    name: str
    type: str
    reference: str | None


class TableInfo(Model):
    name: str
    count: int


class TablePage(Model):
    name: str
    columns: list[TableColumn]
    rows: list[dict[str, JsonValue]]
    next_cursor: str | None
    previews: bool = True


class LogEvent(Model):
    timestamp: datetime
    event: str
    process: str
    fields: dict[str, str | int | float]


class LogPage(Model):
    events: list[LogEvent]
    files_read: int
    truncated: bool
