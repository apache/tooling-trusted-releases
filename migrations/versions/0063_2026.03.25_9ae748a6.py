"""Remove the option to set strict checking

Revision ID: 0063_2026.03.25_9ae748a6
Revises: 0062_2026.03.25_5bc8d2ef
Create Date: 2026-03-25 20:52:42.117365+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0063_2026.03.25_9ae748a6"
down_revision: str | None = "0062_2026.03.25_5bc8d2ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("strict_checking")


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("strict_checking", sa.BOOLEAN(), nullable=False, server_default=sa.false()))
