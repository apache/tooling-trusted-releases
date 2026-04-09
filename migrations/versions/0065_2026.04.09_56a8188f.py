"""Add a table for user sessions

Revision ID: 0065_2026.04.09_56a8188f
Revises: 0064_2026.04.07_2e6c48b8
Create Date: 2026-04-09 14:21:06.309704+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0065_2026.04.09_56a8188f"
down_revision: str | None = "0064_2026.04.07_2e6c48b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usersession",
        sa.Column("sid_hash", sa.String(), nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("dn", sa.String(), nullable=True),
        sa.Column("fullname", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("is_member", sa.Boolean(), nullable=False),
        sa.Column("is_chair", sa.Boolean(), nullable=False),
        sa.Column("is_root", sa.Boolean(), nullable=False),
        sa.Column("committees", sa.JSON(), nullable=False),
        sa.Column("projects", sa.JSON(), nullable=False),
        sa.Column("mfa", sa.Boolean(), nullable=False),
        sa.Column("is_role", sa.Boolean(), nullable=False),
        sa.Column("admin_uid", sa.String(), nullable=True),
        sa.Column("last_account_check", sa.Float(), nullable=True),
        sa.Column("downgrade_admin_to_user", sa.Boolean(), nullable=False),
        sa.Column("cts", sa.Float(), nullable=False),
        sa.Column("uts", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("sid_hash", name=op.f("pk_usersession")),
    )
    with op.batch_alter_table("usersession", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_usersession_admin_uid"), ["admin_uid"], unique=False)
        batch_op.create_index(batch_op.f("ix_usersession_uid"), ["uid"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("usersession", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_usersession_uid"))
        batch_op.drop_index(batch_op.f("ix_usersession_admin_uid"))

    op.drop_table("usersession")
