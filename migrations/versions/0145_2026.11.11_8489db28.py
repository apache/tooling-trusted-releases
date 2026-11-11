"""Add vote interpretations for email votes

Revision ID: 0145_2026.11.11_8489db28
Revises: 0144_2026.11.10_4aa846ad
Create Date: 2026-11-11 14:43:30.409015+00:00
"""

import collections.abc as abc

import alembic.op as op
import sqlalchemy

import atr.models.sql as sql

revision: str = "0145_2026.11.11_8489db28"
down_revision: str | None = "0144_2026.11.10_4aa846ad"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None


def downgrade() -> None:
    op.drop_table("voteinterpretation")


def upgrade() -> None:
    op.create_table(
        "voteinterpretation",
        sqlalchemy.Column("release_key", sqlalchemy.String(), nullable=False),
        sqlalchemy.Column("asf_eid", sqlalchemy.String(), nullable=False),
        sqlalchemy.Column("choice", sqlalchemy.Enum("YES", "ABSTAIN", "NO", name="votechoice"), nullable=False),
        sqlalchemy.Column("asf_uid", sqlalchemy.String(), nullable=False),
        sqlalchemy.Column("created", sql.UTCDateTime(timezone=True), nullable=False),
        sqlalchemy.ForeignKeyConstraint(
            ["release_key"],
            ["release.key"],
            name=op.f("fk_voteinterpretation_release_key_release"),
            ondelete="CASCADE",
        ),
        sqlalchemy.PrimaryKeyConstraint("release_key", "asf_eid", name=op.f("pk_voteinterpretation")),
    )
