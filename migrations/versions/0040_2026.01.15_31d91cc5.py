"""Add schedule column for tasks

Revision ID: 0040_2026.01.15_31d91cc5
Revises: 0039_2026.01.14_cd44f0ea
Create Date: 2026-01-15 15:34:00.515650+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql

# Revision identifiers, used by Alembic
revision: str = "0040_2026.01.15_31d91cc5"
down_revision: str | None = "0039_2026.01.14_cd44f0ea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.add_column(sa.Column("scheduled", atr.models.sql.UTCDateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f("ix_task_scheduled"), ["scheduled"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_task_scheduled"))
        batch_op.drop_column("scheduled")
