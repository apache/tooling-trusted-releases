"""auto_archive

Revision ID: 0084_2026.05.15_07dcce45
Revises: 0083_2026.05.14_7c4f8a2e
Create Date: 2026-05-15 11:11:54.602214+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0084_2026.05.15_07dcce45"
down_revision: str | None = "0083_2026.05.14_7c4f8a2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archive_prior_release", sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("auto_archive_prior_release", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("auto_archive_prior_release")

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("archive_prior_release")
