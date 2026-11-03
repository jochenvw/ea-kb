"""Retain immutable solution assessment reports."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5a1e7c3d9b20"
down_revision: str | None = "e84d9f8a1c27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solution_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("architecture_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_concept_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_version_id", sa.Uuid(), nullable=False),
        sa.Column("conforms", sa.Boolean(), nullable=False),
        sa.Column("acceptable", sa.Boolean(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["architecture_id"], ["solution_architectures.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["solution_architecture_revisions.id"]),
        sa.ForeignKeyConstraint(["baseline_concept_id"], ["concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_solution_assessments_latest",
        "solution_assessments",
        ["architecture_id", "assessed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_solution_assessments_latest", table_name="solution_assessments")
    op.drop_table("solution_assessments")
