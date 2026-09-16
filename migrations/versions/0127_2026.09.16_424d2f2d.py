"""Record the voted ATR revision on releases

Revision ID: 0127_2026.09.16_424d2f2d
Revises: 0126_2026.09.09_bb0de50b
Create Date: 2026-09-16 14:44:41.624907+00:00
"""

from collections.abc import Sequence

import alembic.op as op
import sqlalchemy

revision: str = "0127_2026.09.16_424d2f2d"
down_revision: str | None = "0126_2026.09.09_bb0de50b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sqlalchemy.Column("voted_revision_number", sqlalchemy.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("voted_revision_number")
