"""Add system PAT columns to personal access tokens

Revision ID: 0091_2026.06.01_383a2b3d
Revises: 0090_2026.05.29_a13b8c92
Create Date: 2026-06-01 09:24:31.378879+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0091_2026.06.01_383a2b3d"
down_revision: str | None = "0090_2026.05.29_a13b8c92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Backfill created_by from asfuid for existing rows; all of them predate
    # system PATs, so they are user PATs and the two columns coincide.
    with op.batch_alter_table("personalaccesstoken", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        batch_op.add_column(
            sa.Column("allowed_ip", sa.String(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("created_by", sa.String(), nullable=True),
        )

    op.execute("UPDATE personalaccesstoken SET created_by = asfuid WHERE created_by IS NULL")

    with op.batch_alter_table("personalaccesstoken", schema=None) as batch_op:
        batch_op.alter_column("created_by", existing_type=sa.String(), nullable=False)
        batch_op.alter_column("asfuid", existing_type=sa.String(), nullable=True)
        batch_op.create_index("ix_personalaccesstoken_created_by", ["created_by"], unique=False)


def downgrade() -> None:
    op.execute("DELETE FROM personalaccesstoken WHERE asfuid IS NULL")

    with op.batch_alter_table("personalaccesstoken", schema=None) as batch_op:
        batch_op.drop_index("ix_personalaccesstoken_created_by")
        batch_op.alter_column("asfuid", existing_type=sa.String(), nullable=False)
        batch_op.drop_column("created_by")
        batch_op.drop_column("allowed_ip")
        batch_op.drop_column("is_system")
