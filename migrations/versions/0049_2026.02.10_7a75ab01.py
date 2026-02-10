"""Boolean flag for committees where comitters may release

Revision ID: 0049_2026.02.10_7a75ab01
Revises: 0048_2026.02.06_blocking_to_blocker
Create Date: 2026-02-10 17:49:59.147228+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0049_2026.02.10_7a75ab01"
down_revision: str | None = "0048_2026.02.06_blocking_to_blocker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("committers_may_release", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.drop_column("committers_may_release")
