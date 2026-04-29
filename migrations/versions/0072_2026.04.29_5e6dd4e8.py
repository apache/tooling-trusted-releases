"""Add vote serial and ballot schema

Revision ID: 0072_2026.04.29_5e6dd4e8
Revises: 0071_2026.04.28_bf3c21a9
Create Date: 2026-04-29 13:49:17.604811+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0072_2026.04.29_5e6dd4e8"
down_revision: str | None = "0071_2026.04.28_bf3c21a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "votecounter",
        sa.Column("release_key", sa.String(), nullable=False),
        sa.Column("last_allocated_number", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("release_key", name=op.f("pk_votecounter")),
    )

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("current_vote_seq", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_release_current_vote_seq"), ["current_vote_seq"], unique=False)

    op.create_table(
        "ballotpaper",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("release_key", sa.String(), nullable=False),
        sa.Column("vote_seq", sa.Integer(), nullable=False),
        sa.Column("vote_round", sa.Integer(), nullable=True),
        sa.Column("voter_asf_uid", sa.String(), nullable=False),
        sa.Column("voter_fullname", sa.String(), nullable=False),
        sa.Column("choice", sa.Enum("YES", "ABSTAIN", "NO", name="votechoice"), nullable=False),
        sa.Column("comment", sa.String(), nullable=False, server_default=""),
        sa.Column("is_binding_at_cast", sa.Boolean(), nullable=False),
        sa.Column("revision_number_at_cast", sa.String(), nullable=False),
        sa.Column("receipt_message_id", sa.String(), nullable=False),
        sa.Column("created", sql.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["release_key"],
            ["release.key"],
            name=op.f("fk_ballotpaper_release_key_release"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ballotpaper")),
    )
    op.create_index(op.f("ix_ballotpaper_release_key"), "ballotpaper", ["release_key"], unique=False)
    op.create_index(
        "ix_ballotpaper_release_vote_round_voter_id",
        "ballotpaper",
        ["release_key", "vote_seq", "vote_round", "voter_asf_uid", "id"],
        unique=False,
    )
    op.create_index(
        "ix_ballotpaper_receipt_message_id",
        "ballotpaper",
        ["receipt_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_ballotpaper_release_vote_seq",
        "ballotpaper",
        ["release_key", "vote_seq"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ballotpaper_release_vote_seq", table_name="ballotpaper")
    op.drop_index("ix_ballotpaper_receipt_message_id", table_name="ballotpaper")
    op.drop_index("ix_ballotpaper_release_vote_round_voter_id", table_name="ballotpaper")
    op.drop_index(op.f("ix_ballotpaper_release_key"), table_name="ballotpaper")
    op.drop_table("ballotpaper")

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_release_current_vote_seq"))
        batch_op.drop_column("current_vote_seq")

    op.drop_table("votecounter")
