"""add name to user

Revision ID: 0081_2026.05.13_03b12df9
Revises: 0080_2026.05.13_8df23462
Create Date: 2026-05-13 14:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0081_2026.05.13_03b12df9"
down_revision: str | None = "0080_2026.05.13_8df23462"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("name")
