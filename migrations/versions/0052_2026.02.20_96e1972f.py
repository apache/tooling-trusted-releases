"""Remove revision-based cache option and move to release-based cache busting

Revision ID: 0052_2026.02.20_96e1972f
Revises: 0051_2026.02.17_12ac0c6b
Create Date: 2026-02-20 16:12:21.755711+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0052_2026.02.20_96e1972f"
down_revision: str | None = "0051_2026.02.17_12ac0c6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("check_cache_key", sa.String(), nullable=True))

    with op.batch_alter_table("revision", schema=None) as batch_op:
        batch_op.drop_column("use_check_cache")


def downgrade() -> None:
    with op.batch_alter_table("revision", schema=None) as batch_op:
        batch_op.add_column(sa.Column("use_check_cache", sa.BOOLEAN(), server_default=sa.text("1"), nullable=False))

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("check_cache_key")
