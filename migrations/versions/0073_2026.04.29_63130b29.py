"""Make ballot receipt message IDs unique

Revision ID: 0073_2026.04.29_63130b29
Revises: 0072_2026.04.29_5e6dd4e8
Create Date: 2026-04-29 18:13:34.214393+00:00
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0073_2026.04.29_63130b29"
down_revision: str | None = "0072_2026.04.29_5e6dd4e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_ballotpaper_receipt_message_id", table_name="ballotpaper")
    op.create_index(
        "ix_ballotpaper_receipt_message_id",
        "ballotpaper",
        ["receipt_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_ballotpaper_receipt_message_id", table_name="ballotpaper")
    op.create_index(
        "ix_ballotpaper_receipt_message_id",
        "ballotpaper",
        ["receipt_message_id"],
        unique=False,
    )
