"""Add download_path_suffix to artifacts

Revision ID: 0093_2026.06.04_40cbca79
Revises: 0092_2026.06.02_87d3e8f1
Create Date: 2026-06-04 08:53:16.500003+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0093_2026.06.04_40cbca79"
down_revision: str | None = "0092_2026.06.02_87d3e8f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.add_column(sa.Column("download_path_suffix", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.drop_column("download_path_suffix")
