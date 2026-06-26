"""Add is_archived to release

Revision ID: 0098_2026.06.26_9a948e08
Revises: 0097_2026.06.24_6741a40a
Create Date: 2026-06-26 15:33:58.125528+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0098_2026.06.26_9a948e08"
down_revision: str | None = "0097_2026.06.24_6741a40a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(sa.text("UPDATE release SET is_archived = 1 WHERE archived IS NOT NULL"))


def downgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("is_archived")
