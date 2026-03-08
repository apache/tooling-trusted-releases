"""Add a quarantine status to reflect RM acknowledgement

Revision ID: 0056_2026.03.08_427a333e
Revises: 0055_2026.03.06_522f2417
Create Date: 2026-03-08 15:12:21.172571+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0056_2026.03.08_427a333e"
down_revision: str | None = "0055_2026.03.06_522f2417"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("quarantined", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.VARCHAR(length=7),
            type_=sa.Enum("STAGING", "PENDING", "FAILED", "ACKNOWLEDGED", name="quarantinestatus"),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute("UPDATE quarantined SET status='FAILED' WHERE status='ACKNOWLEDGED'")
    with op.batch_alter_table("quarantined", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum("STAGING", "PENDING", "FAILED", "ACKNOWLEDGED", name="quarantinestatus"),
            type_=sa.Enum("STAGING", "PENDING", "FAILED", name="quarantinestatus"),
            existing_nullable=False,
        )
