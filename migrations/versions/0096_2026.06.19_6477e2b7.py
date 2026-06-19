"""Add expedited to release

Revision ID: 0096_2026.06.19_6477e2b7
Revises: 0095_2026.06.18_0d079d90
Create Date: 2026-06-19 16:20:01.006185+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0096_2026.06.19_6477e2b7"
down_revision: str | None = "0095_2026.06.18_0d079d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("expedited", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("expedited")
