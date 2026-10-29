"""Persist review-only benchmark comparisons and provider caches."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e30bd82f190c"
down_revision = "72ab40e63d15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_comparisons",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("from_edition", sa.String(30), nullable=False),
        sa.Column("to_edition", sa.String(30), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    for column in ("from_edition", "to_edition"):
        op.create_index(f"ix_benchmark_comparisons_{column}", "benchmark_comparisons", [column])
    for table in ("benchmark_embeddings", "benchmark_match_cache"):
        op.create_table(
            table,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
        )


def downgrade() -> None:
    for table in ("benchmark_match_cache", "benchmark_embeddings", "benchmark_comparisons"):
        op.drop_table(table)
