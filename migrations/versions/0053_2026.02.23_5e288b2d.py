"""Add a model for the quarantined phase

Revision ID: 0053_2026.02.23_5e288b2d
Revises: 0052_2026.02.20_96e1972f
Create Date: 2026-02-23 16:53:27.702822+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

revision: str = "0053_2026.02.23_5e288b2d"
down_revision: str | None = "0052_2026.02.20_96e1972f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quarantined",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("release_name", sa.String(), nullable=False),
        sa.Column("asf_uid", sa.String(), nullable=False),
        sa.Column("prior_revision_name", sa.String(), nullable=True),
        sa.Column("status", sa.Enum("PENDING", "FAILED", name="quarantinestatus"), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("created", sql.UTCDateTime(timezone=True), nullable=False),
        sa.Column("completed", sql.UTCDateTime(timezone=True), nullable=True),
        sa.Column("file_metadata", sa.JSON(), nullable=True),
        sa.Column("use_check_cache", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("description", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["release_name"],
            ["release.name"],
            name=op.f("fk_quarantined_release_name_release"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quarantined")),
    )
    with op.batch_alter_table("quarantined", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_quarantined_release_name"), ["release_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_quarantined_status"), ["status"], unique=False)

    with op.batch_alter_table("revision", schema=None) as batch_op:
        batch_op.add_column(sa.Column("was_quarantined", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("revision", schema=None) as batch_op:
        batch_op.drop_column("was_quarantined")

    with op.batch_alter_table("quarantined", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quarantined_status"))
        batch_op.drop_index(batch_op.f("ix_quarantined_release_name"))

    op.drop_table("quarantined")
