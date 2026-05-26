"""session IPs

Revision ID: 0086_2026.05.26_a69c86b6
Revises: 0085_2025.05.19_9d086f7e
Create Date: 2026-05-26 11:44:54.320807+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0086_2026.05.26_a69c86b6"
down_revision: str | None = "0085_2025.05.19_9d086f7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("usersession", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ip_address", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("usersession", schema=None) as batch_op:
        batch_op.drop_column("ip_address")
