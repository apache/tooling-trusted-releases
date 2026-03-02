"""Add a status for quarantine staging

Revision ID: 0054_2026.03.02_3799e8e6
Revises: 0053_2026.02.23_5e288b2d
Create Date: 2026-03-02 19:40:37.748553+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0054_2026.03.02_3799e8e6"
down_revision: str | None = "0053_2026.02.23_5e288b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def downgrade() -> None:
    op.execute("UPDATE quarantined SET status='FAILED' WHERE status='STAGING'")
    with op.batch_alter_table("quarantined", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum("STAGING", "PENDING", "FAILED", name="quarantinestatus"),
            type_=sa.Enum("PENDING", "FAILED", name="quarantinestatus"),
            existing_nullable=False,
        )


def upgrade() -> None:
    with op.batch_alter_table("quarantined", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum("PENDING", "FAILED", name="quarantinestatus"),
            type_=sa.Enum("STAGING", "PENDING", "FAILED", name="quarantinestatus"),
            existing_nullable=False,
        )
