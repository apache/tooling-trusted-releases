"""Add a declared source commit to GitHub SSH keys

Revision ID: 0131_2026.09.24_67d16fe4
Revises: 0130_2026.09.22_d8ffc630
Create Date: 2026-09-24 08:45:15.192266+00:00
"""

import collections.abc as abc

import alembic.op as op
import sqlalchemy

revision: str = "0131_2026.09.24_67d16fe4"
down_revision: str | None = "0130_2026.09.22_d8ffc630"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workflowsshkey") as batch_op:
        batch_op.add_column(sqlalchemy.Column("source_commit", sqlalchemy.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workflowsshkey") as batch_op:
        batch_op.drop_column("source_commit")
