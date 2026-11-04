"""Replace the manual vote mode with the email vote mode

Revision ID: 0141_2026.11.04_4ccf7a32
Revises: 0140_2026.10.30_8b24a608
Create Date: 2026-11-04 13:24:10.991035+00:00
"""

import collections.abc as abc

import alembic.op as op

revision: str = "0141_2026.11.04_4ccf7a32"
down_revision: str | None = "0140_2026.10.30_8b24a608"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None


def downgrade() -> None:
    pass


def upgrade() -> None:
    op.execute("UPDATE release SET vote_mode = 'EMAIL' WHERE vote_mode = 'MANUAL'")
    op.execute("UPDATE releasepolicy SET vote_mode = 'EMAIL' WHERE vote_mode = 'MANUAL'")
