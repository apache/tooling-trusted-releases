"""Add release file state data

Revision ID: 0059_2026.03.16_7dda4775
Revises: 0058_2026.03.12_2ebee77e
Create Date: 2026-03-16 17:10:20.840574+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0059_2026.03.16_7dda4775"
down_revision: str | None = "0058_2026.03.12_2ebee77e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "releasefilestate",
        sa.Column("release_name", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("since_revision_seq", sa.Integer(), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("classification", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["release_name", "since_revision_seq"],
            ["revision.release_name", "revision.seq"],
            name=op.f("fk_releasefilestate_release_name_since_revision_seq_revision"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("release_name", "path", "since_revision_seq", name=op.f("pk_releasefilestate")),
        sa.CheckConstraint(
            """
            (
                (present = 1 AND content_hash IS NOT NULL AND classification IS NOT NULL)
                OR
                (present = 0 AND content_hash IS NULL AND classification IS NULL)
            )
            """,
            name=op.f("ck_releasefilestate_valid_release_file_state"),
        ),
    )


def downgrade() -> None:
    op.drop_table("releasefilestate")
