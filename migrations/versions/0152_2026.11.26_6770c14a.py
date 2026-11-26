"""Record the vote mode of each ballot, so that email ballots stay apart from streamlined ones

Revision ID: 0152_2026.11.26_6770c14a
Revises: 0151_2026.11.24_81cdbaf9
Create Date: 2026-11-26 13:05:44.288716+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0152_2026.11.26_6770c14a"
down_revision: str | None = "0151_2026.11.24_81cdbaf9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every ballot recorded before this migration was cast in a streamlined (Trusted) vote
    with op.batch_alter_table("ballotpaper", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "vote_mode",
                sa.Enum("EMAIL", "TRUSTED", name="votemode"),
                server_default="TRUSTED",
                nullable=False,
            )
        )
    with op.batch_alter_table("ballotpaper", schema=None) as batch_op:
        batch_op.alter_column("vote_mode", server_default=None)


def downgrade() -> None:
    # Without the column, an email ballot would be read as a streamlined one, so email ballots go first
    op.execute("DELETE FROM ballotpaper WHERE vote_mode = 'EMAIL'")
    with op.batch_alter_table("ballotpaper", schema=None) as batch_op:
        batch_op.drop_column("vote_mode")
