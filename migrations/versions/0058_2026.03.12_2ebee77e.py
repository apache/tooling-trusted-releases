"""Make all datetime fields non-nullable

Revision ID: 0058_2026.03.12_2ebee77e
Revises: 0057_2026.03.12_cdf4ce7d
Create Date: 2026-03-12 15:22:42.426540+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0058_2026.03.12_2ebee77e"
down_revision: str | None = "0057_2026.03.12_cdf4ce7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Backfill NULL datetimes before setting nullable=False
    epoch = "1970-01-01 00:00:00"
    op.execute(sa.text(f"UPDATE checkresult SET created = '{epoch}' WHERE created IS NULL"))
    op.execute(sa.text(f"UPDATE checkresultignore SET created = '{epoch}' WHERE created IS NULL"))
    op.execute(sa.text(f"UPDATE personalaccesstoken SET created = '{epoch}' WHERE created IS NULL"))
    op.execute(sa.text(f"UPDATE personalaccesstoken SET expires = '{epoch}' WHERE expires IS NULL"))
    op.execute(sa.text(f"UPDATE project SET created = '{epoch}' WHERE created IS NULL"))
    op.execute(sa.text(f"UPDATE publicsigningkey SET created = '{epoch}' WHERE created IS NULL"))
    op.execute(sa.text(f"UPDATE release SET created = '{epoch}' WHERE created IS NULL"))
    op.execute(sa.text(f"UPDATE revision SET created = '{epoch}' WHERE created IS NULL"))
    op.execute(sa.text(f"UPDATE task SET added = '{epoch}' WHERE added IS NULL"))

    with op.batch_alter_table("checkresult", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=False)

    with op.batch_alter_table("checkresultignore", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=False)

    with op.batch_alter_table("personalaccesstoken", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=False)
        batch_op.alter_column("expires", existing_type=sa.TIMESTAMP(), nullable=False)

    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=False)

    with op.batch_alter_table("publicsigningkey", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=False)

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=False)

    with op.batch_alter_table("revision", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=False)

    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.alter_column("added", existing_type=sa.TIMESTAMP(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.alter_column("added", existing_type=sa.TIMESTAMP(), nullable=True)

    with op.batch_alter_table("revision", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=True)

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=True)

    with op.batch_alter_table("publicsigningkey", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=True)

    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=True)

    with op.batch_alter_table("personalaccesstoken", schema=None) as batch_op:
        batch_op.alter_column("expires", existing_type=sa.TIMESTAMP(), nullable=True)
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=True)

    with op.batch_alter_table("checkresultignore", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=True)

    with op.batch_alter_table("checkresult", schema=None) as batch_op:
        batch_op.alter_column("created", existing_type=sa.TIMESTAMP(), nullable=True)
