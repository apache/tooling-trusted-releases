"""add charter to committee data

Revision ID: 0080_2026.05.13_8df23462
Revises: 0079_2026.05.12_c9f4a1d2
Create Date: 2026-05-13 11:32:31.940938+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0080_2026.05.13_8df23462"
down_revision: str | None = "0079_2026.05.12_c9f4a1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(sa.Column("charter", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.drop_column("charter")
