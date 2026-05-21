"""Add staging_revision_key to distribution

Revision ID: 0086_2026.05.21_38b1cb95
Revises: 0085_2025.05.19_9d086f7e
Create Date: 2026-05-20 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0086_2026.05.21_38b1cb95"
down_revision: str | None = "0085_2025.05.19_9d086f7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Record the revision that was the source of each staged distribution.
    # The column is nullable so that pre-existing rows are unaffected, and
    # so that callers that don't yet know the revision key can still record.
    # See issue #751 for context.
    with op.batch_alter_table("distribution", schema=None) as batch_op:
        batch_op.add_column(sa.Column("staging_revision_key", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_distribution_staging_revision_key_revision"),
            "revision",
            ["staging_revision_key"],
            ["key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("distribution", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_distribution_staging_revision_key_revision"),
            type_="foreignkey",
        )
        batch_op.drop_column("staging_revision_key")
