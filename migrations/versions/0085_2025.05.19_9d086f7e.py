"""Add notification table

Revision ID: 0085_2025.05.19_9d086f7e
Revises: 0084_2026.05.15_07dcce45
Create Date: 2025-05-19 18:20:51.618305+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0085_2025.05.19_9d086f7e"
down_revision: str | None = "0084_2026.05.15_07dcce45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asf_uid", sa.String(), nullable=False),
        sa.Column("created", sql.UTCDateTime(timezone=True), nullable=False),
        sa.Column(
            "level",
            sa.Enum("INFO", "WARNING", "ERROR", name="notificationlevel"),
            nullable=False,
        ),
        sa.Column("message", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification")),
    )
    with op.batch_alter_table("notification", schema=None) as batch_op:
        batch_op.create_index("ix_notification_asf_uid_created", ["asf_uid", "created"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("notification", schema=None) as batch_op:
        batch_op.drop_index("ix_notification_asf_uid_created")
    op.drop_table("notification")
