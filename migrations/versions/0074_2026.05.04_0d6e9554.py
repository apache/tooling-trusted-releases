"""Add ProjectCycle and version metadata for issue 912

Revision ID: 0074_2026.05.04_0d6e9554
Revises: 0073_2026.04.29_63130b29
Create Date: 2026-05-04 11:01:42.318316+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0074_2026.05.04_0d6e9554"
down_revision: str | None = "0073_2026.04.29_63130b29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # project: add version-scheme metadata. Existing rows default to "simple".
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "version_method",
                sa.Enum("SIMPLE", "SEMVER", "CALVER", name="versionmethod"),
                nullable=False,
                server_default="SIMPLE",
            )
        )
        batch_op.add_column(sa.Column("version_pattern", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("cycle_match", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("branch_template", sa.String(), nullable=True))

    # projectcycle: create the table and backfill a "default" cycle for every project.
    op.create_table(
        "projectcycle",
        sa.Column("cycle_key", sa.String(), nullable=False),
        sa.Column("cycle", sa.String(), nullable=False),
        sa.Column("project_key", sa.String(), nullable=False),
        sa.Column("start", sql.UTCDateTime(timezone=True), nullable=True),
        sa.Column("begin", sql.UTCDateTime(timezone=True), nullable=True),
        sa.Column("latest", sql.UTCDateTime(timezone=True), nullable=True),
        sa.Column("eod", sql.UTCDateTime(timezone=True), nullable=True),
        sa.Column("eom", sql.UTCDateTime(timezone=True), nullable=True),
        sa.Column("eol", sql.UTCDateTime(timezone=True), nullable=True),
        sa.Column("lts", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(
            ["project_key"],
            ["project.key"],
            name=op.f("fk_projectcycle_project_key_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("cycle_key", name=op.f("pk_projectcycle")),
        sa.UniqueConstraint("cycle_key", name=op.f("uq_projectcycle_cycle_key")),
        sa.UniqueConstraint("project_key", "cycle", name="unique_project_cycle"),
    )
    op.execute(
        """
        INSERT INTO projectcycle (cycle_key, cycle, project_key, lts)
        SELECT key || '-default', 'default', key, 0
        FROM project
        """
    )

    # release: add cycle_key as nullable, backfill from project_key, then tighten
    # to NOT NULL. New rows are populated via Release.model_post_init in
    # atr/models/sql.py, which fills cycle_key from project_key when not set.
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cycle_key", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_release_cycle_key_projectcycle"),
            "projectcycle",
            ["cycle_key"],
            ["cycle_key"],
        )
    op.execute(
        """
        UPDATE release
        SET cycle_key = project_key || '-default'
        """
    )
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.alter_column("cycle_key", existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_release_cycle_key_projectcycle"), type_="foreignkey")
        batch_op.drop_column("cycle_key")

    op.drop_table("projectcycle")

    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("branch_template")
        batch_op.drop_column("cycle_match")
        batch_op.drop_column("version_pattern")
        batch_op.drop_column("version_method")
