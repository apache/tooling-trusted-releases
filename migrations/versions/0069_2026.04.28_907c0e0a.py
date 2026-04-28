"""Drop release votes

Revision ID: 0069_2026.04.28_907c0e0a
Revises: 0068_2026.04.20_24072a50
Create Date: 2026-04-28 16:09:59.287329+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0069_2026.04.28_907c0e0a"
down_revision: str | None = "0068_2026.04.20_24072a50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("votes")


def downgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("votes", sa.JSON(), nullable=False, server_default="[]"))
