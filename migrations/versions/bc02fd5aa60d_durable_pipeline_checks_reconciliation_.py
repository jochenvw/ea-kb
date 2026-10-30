"""durable pipeline checks reconciliation and issues"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bc02fd5aa60d"
down_revision: str | None = "c9b12e70a641"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "semantic_replay_cache",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("kind IN ('embedding','verification')", name="semantic_replay_kind"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "enrichment_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("check_id", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_enrichment_findings_check_id"), "enrichment_findings", ["check_id"], unique=False
    )
    op.create_index(
        "ix_findings_alias",
        "enrichment_findings",
        ["id"],
        postgresql_where=sa.text("(payload ->> 'action') = 'alias'"),
    )
    op.create_table(
        "knowledge_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("check_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("issue_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_issues_check_id"), "knowledge_issues", ["check_id"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_issues_issue_type"), "knowledge_issues", ["issue_type"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_issues_status"), "knowledge_issues", ["status"], unique=False
    )
    op.create_table(
        "pipeline_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("processor_id", sa.String(length=100), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("kind IN ('pipeline','stage','check')"),
        sa.CheckConstraint("status IN ('running','done','failed','cancelled','skipped')"),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["pipeline_executions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_cache",
        "pipeline_executions",
        ["processor_id", "fingerprint", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pipeline_executions_parent_id"), "pipeline_executions", ["parent_id"], unique=False
    )
    op.create_table(
        "pipeline_leases",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("owner", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "issue_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["knowledge_issues.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_issue_events_issue_id"), "issue_events", ["issue_id"], unique=False)
    op.create_table(
        "reconciliation_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("previous_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("outcome IN ('accepted','rejected','deferred','reversed')"),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["enrichment_findings.id"],
        ),
        sa.ForeignKeyConstraint(
            ["previous_id"],
            ["reconciliation_decisions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("finding_id", "previous_id"),
    )
    op.create_index(
        "ix_decision_head",
        "reconciliation_decisions",
        ["finding_id", "recorded_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_decision_head", table_name="reconciliation_decisions")
    op.drop_table("reconciliation_decisions")
    op.drop_index(op.f("ix_issue_events_issue_id"), table_name="issue_events")
    op.drop_table("issue_events")
    op.drop_table("pipeline_leases")
    op.drop_index(op.f("ix_pipeline_executions_parent_id"), table_name="pipeline_executions")
    op.drop_index("ix_execution_cache", table_name="pipeline_executions")
    op.drop_table("pipeline_executions")
    op.drop_index(op.f("ix_knowledge_issues_status"), table_name="knowledge_issues")
    op.drop_index(op.f("ix_knowledge_issues_issue_type"), table_name="knowledge_issues")
    op.drop_index(op.f("ix_knowledge_issues_check_id"), table_name="knowledge_issues")
    op.drop_table("knowledge_issues")
    op.drop_index("ix_findings_alias", table_name="enrichment_findings")
    op.drop_index(op.f("ix_enrichment_findings_check_id"), table_name="enrichment_findings")
    op.drop_table("enrichment_findings")
    op.drop_table("semantic_replay_cache")
