"""add updated and updated_by to committee and project

Revision ID: 0082_2026.05.14_a1b2c3d4
Revises: 0081_2026.05.13_03b12df9
Create Date: 2026-05-14 11:30:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0082_2026.05.14_a1b2c3d4"
down_revision: str | None = "0081_2026.05.13_03b12df9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(sa.Column("updated", sql.UTCDateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("updated_by", sa.String(), nullable=True))

    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sa.Column("updated", sql.UTCDateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("updated_by", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("updated_by")
        batch_op.drop_column("updated")

    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.drop_column("updated_by")
        batch_op.drop_column("updated")
