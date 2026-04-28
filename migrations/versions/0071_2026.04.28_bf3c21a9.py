"""Remove manual vote booleans

Revision ID: 0071_2026.04.28_bf3c21a9
Revises: 0070_2026.04.28_87620288
Create Date: 2026-04-28 17:59:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071_2026.04.28_bf3c21a9"
down_revision: str | None = "0070_2026.04.28_87620288"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("vote_mode", sa.Enum("MANUAL", "EMAIL", "TRUSTED", name="votemode")))

    op.execute(
        sa.text(
            """
            UPDATE release
            SET vote_mode = CASE
                WHEN phase = 'RELEASE_CANDIDATE_DRAFT' THEN NULL
                WHEN vote_manual = 1 THEN 'MANUAL'
                ELSE 'EMAIL'
            END
            """
        )
    )

    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("manual_vote")

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("vote_manual")


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("manual_vote", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.execute(sa.text("UPDATE releasepolicy SET manual_vote = 1 WHERE vote_mode = 'MANUAL'"))
    op.execute(sa.text("UPDATE releasepolicy SET manual_vote = 0 WHERE vote_mode != 'MANUAL'"))

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("vote_manual", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.execute(sa.text("UPDATE release SET vote_manual = 1 WHERE vote_mode = 'MANUAL'"))
    # Not strictly necessary, because False (0) is the default
    op.execute(sa.text("UPDATE release SET vote_manual = 0 WHERE vote_mode != 'MANUAL' OR vote_mode IS NULL"))

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("vote_mode")
