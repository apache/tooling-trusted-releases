"""Add managed and dated to artifact

Revision ID: 0097_2026.06.24_6741a40a
Revises: 0096_2026.06.19_6477e2b7
Create Date: 2026-06-24 09:13:47.201496+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0097_2026.06.24_6741a40a"
down_revision: str | None = "0096_2026.06.19_6477e2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.add_column(sa.Column("managed", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("dated", sql.UTCDateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.drop_column("dated")
        batch_op.drop_column("managed")
