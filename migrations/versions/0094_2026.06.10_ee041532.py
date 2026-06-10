"""Add sbom_path to artifacts

Revision ID: 0094_2026.06.10_ee041532
Revises: 0093_2026.06.04_40cbca79
Create Date: 2026-06-10 14:29:28.394235+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0094_2026.06.10_ee041532"
down_revision: str | None = "0093_2026.06.04_40cbca79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sbom_path", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.drop_column("sbom_path")
