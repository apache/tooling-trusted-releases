"""Use current RevisionCounter values and switch the allocation logic

Revision ID: 0039_2026.01.14_cd44f0ea
Revises: 0038_2026.01.14_267562c1
Create Date: 2026-01-14 16:12:28.673361+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039_2026.01.14_cd44f0ea"
down_revision: str | None = "0038_2026.01.14_267562c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Update RevisionCounter.last_allocated_number to max(seq) for each release
    # This must be run before the new allocation logic can be used
    op.execute("""
        UPDATE revisioncounter
        SET last_allocated_number = COALESCE(
            (SELECT MAX(seq) FROM revision WHERE revision.release_name = revisioncounter.release_name),
            0
        )
    """)


def downgrade() -> None:
    op.execute("UPDATE revisioncounter SET last_allocated_number = 0")
