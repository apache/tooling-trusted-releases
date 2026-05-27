"""Add finish_vote_template to release policies

Revision ID: 0088_2026.05.27_449e0006
Revises: 0087_2026.05.26_3eabb86e
Create Date: 2026-05-27 11:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0088_2026.05.27_449e0006"
down_revision: str | None = "0087_2026.05.26_3eabb86e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("finish_vote_template", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("finish_vote_template")
