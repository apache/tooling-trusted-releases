"""Add Release.archived for CLE archive event

Revision ID: 0075_2026.05.04_97e3037f
Revises: 0074_2026.05.04_0d6e9554
Create Date: 2026-05-04 15:50:32.864662+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0075_2026.05.04_97e3037f"
down_revision: str | None = "0074_2026.05.04_0d6e9554"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archived", sql.UTCDateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("archived")
