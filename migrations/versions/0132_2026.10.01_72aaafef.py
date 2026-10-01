"""Enforce expedited release vote mode

Revision ID: 0132_2026.10.01_72aaafef
Revises: 0131_2026.09.24_67d16fe4
Create Date: 2026-10-01 14:09:37.591716+00:00
"""

import collections.abc as abc

import alembic.op as op
import sqlalchemy

revision: str = "0132_2026.10.01_72aaafef"
down_revision: str | None = "0131_2026.09.24_67d16fe4"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None


def downgrade() -> None:
    with op.batch_alter_table("release") as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_release_expedited_vote_mode_trusted"), type_="check")


def upgrade() -> None:
    invalid_key = op.get_bind().scalar(
        sqlalchemy.text(
            "SELECT key FROM release WHERE expedited AND vote_mode IS NOT NULL AND vote_mode != 'TRUSTED' LIMIT 1"
        )
    )
    if invalid_key is not None:
        raise RuntimeError(f"Expedited release {invalid_key} has a non-trusted vote mode; resolve it before migrating")
    with op.batch_alter_table("release") as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_release_expedited_vote_mode_trusted"),
            "NOT expedited OR vote_mode IS NULL OR vote_mode = 'TRUSTED'",
        )
