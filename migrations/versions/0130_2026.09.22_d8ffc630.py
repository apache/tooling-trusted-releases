"""Identify admin notifications

Revision ID: 0130_2026.09.22_d8ffc630
Revises: 0129_2026.09.18_164eae40
Create Date: 2026-09-22 14:49:57.603377+00:00
"""

import collections.abc as abc

import alembic.op as op
import sqlalchemy

revision: str = "0130_2026.09.22_d8ffc630"
down_revision: str | None = "0129_2026.09.18_164eae40"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None


def downgrade() -> None:
    with op.batch_alter_table("notification") as batch_op:
        batch_op.drop_column("is_admin")


def upgrade() -> None:
    with op.batch_alter_table("notification") as batch_op:
        batch_op.add_column(
            sqlalchemy.Column("is_admin", sqlalchemy.Boolean(), nullable=False, server_default=sqlalchemy.false())
        )
