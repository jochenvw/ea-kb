from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ekb.domain.assertions import checked_assertion
from ekb.domain.models import Assertion, ConceptVersion, RelationshipKind, SemanticType, now


class Base(DeclarativeBase):
    pass


class SourceRow(Base):
    __tablename__ = "sources"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    connector: Mapped[str] = mapped_column(String(100), index=True)
    location: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RevisionRow(Base):
    __tablename__ = "source_revisions"
    __table_args__ = (UniqueConstraint("source_id", "content_hash"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    predecessor_id: Mapped[UUID | None] = mapped_column(ForeignKey("source_revisions.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class PublicationRow(Base):
    __tablename__ = "publication_assessments"
    __table_args__ = (UniqueConstraint("revision_id", "fingerprint"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("source_revisions.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SegmentRow(Base):
    __tablename__ = "segments"
    __table_args__ = (UniqueConstraint("revision_id", "parser_fingerprint", "ordinal"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("source_revisions.id"), index=True)
    parser_fingerprint: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("segments.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ConceptRow(Base):
    __tablename__ = "concepts"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    identity_key: Mapped[str] = mapped_column(Text, unique=True)


class VersionRow(Base):
    __tablename__ = "concept_versions"
    __table_args__ = (
        UniqueConstraint("concept_id", "semantic_version"),
        CheckConstraint(
            "semantic_type IN (" + ",".join(repr(value.value) for value in SemanticType) + ")",
            name="version_semantic_type",
        ),
        CheckConstraint(
            "semantic_type IS NOT DISTINCT FROM (payload #>> '{candidate,semantic_type}')",
            name="version_semantic_type_payload",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    concept_id: Mapped[UUID] = mapped_column(ForeignKey("concepts.id"), index=True)
    semantic_type: Mapped[str | None] = mapped_column(String(40), index=True)
    semantic_version: Mapped[str | None] = mapped_column(Text)
    predecessor_id: Mapped[UUID | None] = mapped_column(ForeignKey("concept_versions.id"))
    statement: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    assertion: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def to_domain(self) -> ConceptVersion:
        version = ConceptVersion.model_validate(self.payload)
        assertion = (
            checked_assertion(version.candidate, Assertion.model_validate(self.assertion))
            if self.assertion
            else None
        )
        return version.model_copy(update={"assertion": assertion})


class OccurrenceRow(Base):
    __tablename__ = "occurrences"
    __table_args__ = (
        UniqueConstraint("concept_version_id", "segment_id", "start_offset", "end_offset"),
        CheckConstraint("start_offset >= 0 AND end_offset > start_offset"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    concept_version_id: Mapped[UUID] = mapped_column(ForeignKey("concept_versions.id"), index=True)
    segment_id: Mapped[UUID] = mapped_column(ForeignKey("segments.id"), index=True)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RelationshipRow(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("from_id", "to_id", "kind", "occurrence_id"),
        CheckConstraint("from_id <> to_id"),
        CheckConstraint(
            "kind IN (" + ",".join(repr(value.value) for value in RelationshipKind) + ")",
            name="ontology_relationship_kind",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    from_id: Mapped[UUID] = mapped_column(ForeignKey("concepts.id"), index=True)
    to_id: Mapped[UUID] = mapped_column(ForeignKey("concepts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    occurrence_id: Mapped[UUID] = mapped_column(ForeignKey("occurrences.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AuthorityRow(Base):
    __tablename__ = "authority_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('review', 'approve', 'reject', 'promote', 'withdraw')",
            name="authority_action",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    concept_version_id: Mapped[UUID] = mapped_column(ForeignKey("concept_versions.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RunRow(Base):
    __tablename__ = "processing_runs"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class TaskRow(Base):
    __tablename__ = "processing_tasks"
    __table_args__ = (
        UniqueConstraint("revision_id", "fingerprint"),
        CheckConstraint("status IN ('pending','running','done','failed','cancelled')"),
        Index("ix_tasks_status_lease", "status", "lease_until"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("source_revisions.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    owner: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SegmentResultRow(Base):
    __tablename__ = "segment_results"
    __table_args__ = (UniqueConstraint("segment_id", "inference_fingerprint"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    segment_id: Mapped[UUID] = mapped_column(ForeignKey("segments.id"), index=True)
    inference_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ParseRow(Base):
    __tablename__ = "parse_checkpoints"
    __table_args__ = (UniqueConstraint("revision_id", "parser_fingerprint"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("source_revisions.id"))
    parser_fingerprint: Mapped[str] = mapped_column(String(64))


class BenchmarkDocumentRow(Base):
    __tablename__ = "benchmark_documents"
    __table_args__ = (UniqueConstraint("revision_id", "parser_version", "extractor_version"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("source_revisions.id"), index=True)
    segment_id: Mapped[UUID] = mapped_column(ForeignKey("segments.id"), index=True)
    edition: Mapped[str] = mapped_column(String(30), index=True)
    role: Mapped[str] = mapped_column(String(30))
    parser_version: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class BenchmarkComparisonRow(Base):
    __tablename__ = "benchmark_comparisons"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    from_edition: Mapped[str] = mapped_column(String(30), index=True)
    to_edition: Mapped[str] = mapped_column(String(30), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class BenchmarkEmbeddingRow(Base):
    __tablename__ = "benchmark_embeddings"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class BenchmarkMatchRow(Base):
    __tablename__ = "benchmark_match_cache"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ExecutionRow(Base):
    __tablename__ = "pipeline_executions"
    __table_args__ = (
        CheckConstraint("kind IN ('pipeline','stage','check')"),
        CheckConstraint("status IN ('running','done','failed','cancelled','skipped')"),
        Index("ix_execution_cache", "processor_id", "fingerprint", "status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("pipeline_executions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    processor_id: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class WorkflowLeaseRow(Base):
    __tablename__ = "pipeline_leases"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner: Mapped[UUID]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FindingRow(Base):
    __tablename__ = "enrichment_findings"
    __table_args__ = (
        Index(
            "ix_findings_alias",
            "id",
            postgresql_where=text("(payload ->> 'action') = 'alias'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    check_id: Mapped[str] = mapped_column(String(100), index=True)
    input_revision: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class DecisionRow(Base):
    __tablename__ = "reconciliation_decisions"
    __table_args__ = (
        CheckConstraint("outcome IN ('accepted','rejected','deferred','reversed')"),
        UniqueConstraint("finding_id", "previous_id"),
        Index("ix_decision_head", "finding_id", "recorded_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    finding_id: Mapped[UUID] = mapped_column(ForeignKey("enrichment_findings.id"))
    previous_id: Mapped[UUID | None] = mapped_column(ForeignKey("reconciliation_decisions.id"))
    outcome: Mapped[str] = mapped_column(String(20))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class IssueRow(Base):
    __tablename__ = "knowledge_issues"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    check_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    issue_type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class IssueEventRow(Base):
    __tablename__ = "issue_events"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    issue_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_issues.id"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
