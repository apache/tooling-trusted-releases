"""Add check exclusion properties to release policies

Revision ID: 0035_2026.01.08_2bbfd636
Revises: 0034_2025.12.31_ac4dcf44
Create Date: 2026-01-08 19:52:50.017456+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0035_2026.01.08_2bbfd636"
down_revision: str | None = "0034_2025.12.31_ac4dcf44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_excludes_lightweight", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("source_excludes_rat", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("source_excludes_rat")
        batch_op.drop_column("source_excludes_lightweight")
