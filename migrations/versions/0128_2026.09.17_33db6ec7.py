"""Store committee mailing list metadata

Revision ID: 0128_2026.09.17_33db6ec7
Revises: 0127_2026.09.16_424d2f2d
Create Date: 2026-09-17 19:41:20.083427+00:00
"""

import collections.abc as abc

import alembic.op as op
import sqlalchemy

revision: str = "0128_2026.09.17_33db6ec7"
down_revision: str | None = "0127_2026.09.16_424d2f2d"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None


def downgrade() -> None:
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.drop_column("mail_addresses")


def upgrade() -> None:
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(sqlalchemy.Column("mail_addresses", sqlalchemy.JSON(), nullable=False, server_default="[]"))
