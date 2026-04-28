"""Add release policy vote mode

Revision ID: 0070_2026.04.28_87620288
Revises: 0069_2026.04.28_907c0e0a
Create Date: 2026-04-28 16:45:09.407049+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0070_2026.04.28_87620288"
down_revision: str | None = "0069_2026.04.28_907c0e0a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "vote_mode",
                sa.Enum("MANUAL", "EMAIL", "TRUSTED", name="votemode"),
                nullable=False,
                server_default="EMAIL",
            )
        )

    op.execute(sa.text("UPDATE releasepolicy SET vote_mode = 'MANUAL' WHERE manual_vote = 1"))
    # This is not strictly necessary, because "EMAIL" is the default
    op.execute(sa.text("UPDATE releasepolicy SET vote_mode = 'EMAIL' WHERE manual_vote = 0"))


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("vote_mode")
