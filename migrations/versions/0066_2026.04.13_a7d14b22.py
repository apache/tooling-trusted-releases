"""Add sessionformerror table for server-side form error flashes

Revision ID: 0066_2026.04.13_a7d14b22
Revises: 0065_2026.04.09_56a8188f
Create Date: 2026-04-13 11:57:02.301243+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0066_2026.04.13_a7d14b22"
down_revision: str | None = "0065_2026.04.09_56a8188f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessionformerror",
        sa.Column("sid_hash", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("cts", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("sid_hash", "path", name=op.f("pk_sessionformerror")),
    )


def downgrade() -> None:
    op.drop_table("sessionformerror")
