"""Add 3-way merge metadata to revisions

Revision ID: 0055_2026.03.06_522f2417
Revises: 0054_2026.03.02_3799e8e6
Create Date: 2026-03-06 15:22:14.422556+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0055_2026.03.06_522f2417"
down_revision: str | None = "0054_2026.03.02_3799e8e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("revision", schema=None) as batch_op:
        batch_op.add_column(sa.Column("merge_base_revision_name", sa.VARCHAR(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("revision", schema=None) as batch_op:
        batch_op.drop_column("merge_base_revision_name")
