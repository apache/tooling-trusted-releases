"""Add ATR tagging spec policy

Revision ID: 0041_2026.01.21_44cdc6b9
Revises: 0040_2026.01.15_31d91cc5
Create Date: 2026-01-21 15:52:13.681523+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0041_2026.01.21_44cdc6b9"
down_revision: str | None = "0040_2026.01.15_31d91cc5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("atr_file_tagging_spec", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("atr_file_tagging_spec")
