"""Make all list fields non-nullable

Revision ID: 0057_2026.03.12_cdf4ce7d
Revises: 0056_2026.03.08_427a333e
Create Date: 2026-03-12 14:50:05.930579+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import sqlite

# Revision identifiers, used by Alembic
revision: str = "0057_2026.03.12_cdf4ce7d"
down_revision: str | None = "0056_2026.03.08_427a333e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Backfill NULL -> [] before setting nullable=False
    op.execute(sa.text("UPDATE committee SET committee_members = '[]' WHERE committee_members IS NULL"))
    op.execute(sa.text("UPDATE committee SET committers = '[]' WHERE committers IS NULL"))
    op.execute(sa.text("UPDATE committee SET release_managers = '[]' WHERE release_managers IS NULL"))
    op.execute(
        sa.text("UPDATE publicsigningkey SET secondary_declared_uids = '[]' WHERE secondary_declared_uids IS NULL")
    )
    op.execute(sa.text("UPDATE release SET package_managers = '[]' WHERE package_managers IS NULL"))
    op.execute(sa.text("UPDATE release SET sboms = '[]' WHERE sboms IS NULL"))
    op.execute(sa.text("UPDATE release SET votes = '[]' WHERE votes IS NULL"))
    op.execute(sa.text("UPDATE releasepolicy SET mailto_addresses = '[]' WHERE mailto_addresses IS NULL"))
    op.execute(sa.text("UPDATE releasepolicy SET binary_artifact_paths = '[]' WHERE binary_artifact_paths IS NULL"))
    op.execute(sa.text("UPDATE releasepolicy SET source_artifact_paths = '[]' WHERE source_artifact_paths IS NULL"))

    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.alter_column("committee_members", existing_type=sqlite.JSON(), nullable=False)
        batch_op.alter_column("committers", existing_type=sqlite.JSON(), nullable=False)
        batch_op.alter_column("release_managers", existing_type=sqlite.JSON(), nullable=False)

    with op.batch_alter_table("publicsigningkey", schema=None) as batch_op:
        batch_op.alter_column("secondary_declared_uids", existing_type=sqlite.JSON(), nullable=False)

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.alter_column("package_managers", existing_type=sqlite.JSON(), nullable=False)
        batch_op.alter_column("sboms", existing_type=sqlite.JSON(), nullable=False)
        batch_op.alter_column("votes", existing_type=sqlite.JSON(), nullable=False)

    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.alter_column("mailto_addresses", existing_type=sqlite.JSON(), nullable=False)
        batch_op.alter_column("binary_artifact_paths", existing_type=sqlite.JSON(), nullable=False)
        batch_op.alter_column("source_artifact_paths", existing_type=sqlite.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.alter_column("source_artifact_paths", existing_type=sqlite.JSON(), nullable=True)
        batch_op.alter_column("binary_artifact_paths", existing_type=sqlite.JSON(), nullable=True)
        batch_op.alter_column("mailto_addresses", existing_type=sqlite.JSON(), nullable=True)

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.alter_column("votes", existing_type=sqlite.JSON(), nullable=True)
        batch_op.alter_column("sboms", existing_type=sqlite.JSON(), nullable=True)
        batch_op.alter_column("package_managers", existing_type=sqlite.JSON(), nullable=True)

    with op.batch_alter_table("publicsigningkey", schema=None) as batch_op:
        batch_op.alter_column("secondary_declared_uids", existing_type=sqlite.JSON(), nullable=True)

    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.alter_column("release_managers", existing_type=sqlite.JSON(), nullable=True)
        batch_op.alter_column("committers", existing_type=sqlite.JSON(), nullable=True)
        batch_op.alter_column("committee_members", existing_type=sqlite.JSON(), nullable=True)
