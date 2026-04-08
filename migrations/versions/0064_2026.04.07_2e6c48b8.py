"""user table

Revision ID: 0064_2026.04.07_2e6c48b8
Revises: 0063_2026.03.25_9ae748a6
Create Date: 2026-04-07 14:16:54.960772+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0064_2026.04.07_2e6c48b8"
down_revision: str | None = "0063_2026.03.25_9ae748a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("asfuid", sa.String(), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("asfuid", name=op.f("pk_user")),
    )


def downgrade() -> None:
    op.drop_table("user")
