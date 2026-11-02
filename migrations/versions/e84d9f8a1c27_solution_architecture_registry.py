"""Add immutable solution architecture registry records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e84d9f8a1c27"
down_revision: str | None = "ab73c82d104e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solution_architectures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "solution_architecture_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("architecture_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["architecture_id"],
            ["solution_architectures.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("architecture_id", "version"),
    )
    op.create_index(
        op.f("ix_solution_architecture_revisions_architecture_id"),
        "solution_architecture_revisions",
        ["architecture_id"],
        unique=False,
    )
    op.create_table(
        "solution_architecture_controls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("concept_id", sa.Uuid(), nullable=False),
        sa.Column("disposition", sa.String(length=20), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.CheckConstraint(
            "disposition IN ('required','exception')",
            name="solution_architecture_control_disposition",
        ),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"]),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["solution_architecture_revisions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "concept_id"),
    )
    op.create_index(
        op.f("ix_solution_architecture_controls_concept_id"),
        "solution_architecture_controls",
        ["concept_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_solution_architecture_controls_revision_id"),
        "solution_architecture_controls",
        ["revision_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_solution_architecture_controls_revision_id"),
        table_name="solution_architecture_controls",
    )
    op.drop_index(
        op.f("ix_solution_architecture_controls_concept_id"),
        table_name="solution_architecture_controls",
    )
    op.drop_table("solution_architecture_controls")
    op.drop_index(
        op.f("ix_solution_architecture_revisions_architecture_id"),
        table_name="solution_architecture_revisions",
    )
    op.drop_table("solution_architecture_revisions")
    op.drop_table("solution_architectures")
