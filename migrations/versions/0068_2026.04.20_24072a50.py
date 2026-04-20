"""Add release file generation provenance

Revision ID: 0068_2026.04.20_24072a50
Revises: 0067_2026.04.14_bbfd41d3
Create Date: 2026-04-20 15:39:52.177891+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql

# Revision identifiers, used by Alembic
revision: str = "0068_2026.04.20_24072a50"
down_revision: str | None = "0067_2026.04.14_bbfd41d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releasefilestate", schema=None) as batch_op:
        batch_op.add_column(sa.Column("provenance", atr.models.sql.SafeJSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("releasefilestate", schema=None) as batch_op:
        batch_op.drop_column("provenance")
