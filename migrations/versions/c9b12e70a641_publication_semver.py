"""Separate publication evidence, unknown versions and scoped authority."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9b12e70a641"
down_revision = "e2a0f34cf236"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publication_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("revision_id", sa.Uuid(), sa.ForeignKey("source_revisions.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("revision_id", "fingerprint"),
    )
    op.create_index(
        "ix_publication_assessments_revision_id", "publication_assessments", ["revision_id"]
    )
    op.alter_column("concept_versions", "semantic_version", type_=sa.Text(), nullable=True)
    # Initial MVP versions were generated counters, not source-backed semantic classifications.
    op.execute("""
        UPDATE concept_versions
        SET semantic_version = NULL,
            payload = (payload - 'version') || '{"semantic_version": null}'::jsonb
        WHERE predecessor_id IS NULL AND semantic_version = '1.0.0'
    """)
    op.drop_constraint("authority_events_action_check", "authority_events", type_="check")
    op.create_check_constraint(
        "authority_action",
        "authority_events",
        "action IN ('review', 'approve', 'reject', 'promote', 'withdraw')",
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Publication history and unknown semantic versions require an explicit data migration"
    )
