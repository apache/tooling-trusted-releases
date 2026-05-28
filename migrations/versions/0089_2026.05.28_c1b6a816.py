"""Replace release policy mailto_addresses with recipient_defaults

Revision ID: 0089_2026.05.28_c1b6a816
Revises: 0088_2026.05.27_449e0006
Create Date: 2026-05-28 09:00:18.337802+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0089_2026.05.28_c1b6a816"
down_revision: str | None = "0088_2026.05.27_449e0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("recipient_defaults", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.drop_column("mailto_addresses")


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mailto_addresses", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.drop_column("recipient_defaults")
