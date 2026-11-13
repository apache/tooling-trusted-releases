"""Add quota overrides to projects

Revision ID: 0147_2026.11.13_bb0b30bf
Revises: 0146_2026.11.13_a52e1080
Create Date: 2026-11-13 15:04:47.099560+00:00
"""

import collections.abc as abc

import alembic.op as op
import sqlalchemy

revision: str = "0147_2026.11.13_bb0b30bf"
down_revision: str | None = "0146_2026.11.13_a52e1080"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None


def downgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("quota_release_files")
        batch_op.drop_column("quota_release_bytes")
        batch_op.drop_column("quota_project_releases")
        batch_op.drop_column("quota_project_bytes")


def upgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sqlalchemy.Column("quota_project_bytes", sqlalchemy.Integer(), nullable=True))
        batch_op.add_column(sqlalchemy.Column("quota_project_releases", sqlalchemy.Integer(), nullable=True))
        batch_op.add_column(sqlalchemy.Column("quota_release_bytes", sqlalchemy.Integer(), nullable=True))
        batch_op.add_column(sqlalchemy.Column("quota_release_files", sqlalchemy.Integer(), nullable=True))
