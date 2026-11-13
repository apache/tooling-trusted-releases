"""Add size to release file state

Revision ID: 0146_2026.11.13_a52e1080
Revises: 0145_2026.11.11_8489db28
Create Date: 2026-11-13 14:34:10.611316+00:00
"""

import collections.abc as abc

import alembic.op as op
import sqlalchemy

revision: str = "0146_2026.11.13_a52e1080"
down_revision: str | None = "0145_2026.11.11_8489db28"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None


def downgrade() -> None:
    with op.batch_alter_table("releasefilestate", schema=None) as batch_op:
        batch_op.drop_column("size")


def upgrade() -> None:
    with op.batch_alter_table("releasefilestate", schema=None) as batch_op:
        batch_op.add_column(sqlalchemy.Column("size", sqlalchemy.Integer(), nullable=True))
