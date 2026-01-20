"""Add a RevisionCounter table to ensure the persistence of serial numbers

Revision ID: 0038_2026.01.14_267562c1
Revises: 0037_2026.01.13_0cefcaea
Create Date: 2026-01-14 15:52:09.557729+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0038_2026.01.14_267562c1"
down_revision: str | None = "0037_2026.01.13_0cefcaea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "revisioncounter",
        sa.Column("release_name", sa.String(), nullable=False),
        sa.Column("last_allocated_number", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("release_name", name=op.f("pk_revisioncounter")),
    )

    # Populate from existing releases with last_allocated_number=0
    # This allows manual verification before switching the allocation logic
    # The actual MAX(seq) values are to be set in a subsequent migration
    op.execute("""
        INSERT INTO revisioncounter (release_name, last_allocated_number)
        SELECT name, 0
        FROM release
    """)


def downgrade() -> None:
    op.drop_table("revisioncounter")
