"""Cache concise assertions independently of immutable extracted versions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ab73c82d104e"
down_revision = "bc02fd5aa60d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("concept_versions", sa.Column("assertion", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("concept_versions", "assertion")
