"""Remove the policy option to pause for the release manager

Revision ID: 0062_2026.03.25_5bc8d2ef
Revises: 0061_2026.03.18_7838cfcc
Create Date: 2026-03-25 15:13:05.016111+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0062_2026.03.25_5bc8d2ef"
down_revision: str | None = "0061_2026.03.18_7838cfcc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("pause_for_rm")


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pause_for_rm", sa.BOOLEAN(), nullable=False, server_default=sa.false()))
