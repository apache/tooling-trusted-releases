"""check version into database

Revision ID: 0061_2026.03.18_7838cfcc
Revises: 0060_2026.03.16_2c8e4716
Create Date: 2026-03-18 13:51:12.776504+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0061_2026.03.18_7838cfcc"
down_revision: str | None = "0060_2026.03.16_2c8e4716"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("checkresult", schema=None) as batch_op:
        batch_op.add_column(sa.Column("checker_version", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("checkresult", schema=None) as batch_op:
        batch_op.drop_column("checker_version")
