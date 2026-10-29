"""Persist immutable structured benchmark records and exact source bindings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "72ab40e63d15"
down_revision = "c9b12e70a641"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("revision_id", sa.Uuid(), sa.ForeignKey("source_revisions.id"), nullable=False),
        sa.Column("segment_id", sa.Uuid(), sa.ForeignKey("segments.id"), nullable=False),
        sa.Column("edition", sa.String(30), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("revision_id", "parser_version", "extractor_version"),
    )
    for name in ("source_id", "revision_id", "segment_id", "edition"):
        op.create_index(f"ix_benchmark_documents_{name}", "benchmark_documents", [name])


def downgrade() -> None:
    op.drop_table("benchmark_documents")
