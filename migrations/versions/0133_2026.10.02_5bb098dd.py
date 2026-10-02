"""Remove release archival approval requests

Revision ID: 0133_2026.10.02_5bb098dd
Revises: 0132_2026.10.01_72aaafef
Create Date: 2026-10-02 10:20:37.669985+00:00
"""

import collections.abc as abc

import alembic.op as op
import sqlalchemy

revision: str = "0133_2026.10.02_5bb098dd"
down_revision: str | None = "0132_2026.10.01_72aaafef"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None


def upgrade() -> None:
    active_id = op.get_bind().scalar(
        sqlalchemy.text(
            "SELECT id FROM approvalrequest"
            " WHERE action = 'ARCHIVE_RELEASE' AND status IN ('PENDING', 'APPROVED') LIMIT 1"
        )
    )
    if active_id is not None:
        raise RuntimeError(
            f"Release archival approval request {active_id} is still active; resolve it before migrating"
        )
    op.execute(sqlalchemy.text("DELETE FROM approvalrequest WHERE action = 'ARCHIVE_RELEASE'"))

    op.drop_index("ix_approvalrequest_active_release", table_name="approvalrequest")
    op.drop_index("ix_approvalrequest_active_project", table_name="approvalrequest")
    op.create_index(
        "ix_approvalrequest_active_project",
        "approvalrequest",
        ["project_key"],
        unique=True,
        sqlite_where=sqlalchemy.text("status IN ('PENDING', 'APPROVED')"),
    )

    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.alter_column(
            "action",
            existing_type=sqlalchemy.Enum("ARCHIVE", "ARCHIVE_RELEASE", "DELETE", name="approvalaction"),
            type_=sqlalchemy.Enum("ARCHIVE", "DELETE", name="approvalaction"),
            existing_nullable=False,
        )
        batch_op.drop_column("release_version")


def downgrade() -> None:
    # The release archival requests deleted on upgrade can't be restored
    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.add_column(sqlalchemy.Column("release_version", sqlalchemy.String(), nullable=True))
        batch_op.alter_column(
            "action",
            existing_type=sqlalchemy.Enum("ARCHIVE", "DELETE", name="approvalaction"),
            type_=sqlalchemy.Enum("ARCHIVE", "ARCHIVE_RELEASE", "DELETE", name="approvalaction"),
            existing_nullable=False,
        )

    op.drop_index("ix_approvalrequest_active_project", table_name="approvalrequest")
    op.create_index(
        "ix_approvalrequest_active_project",
        "approvalrequest",
        ["project_key"],
        unique=True,
        sqlite_where=sqlalchemy.text("status IN ('PENDING', 'APPROVED') AND release_version IS NULL"),
    )
    op.create_index(
        "ix_approvalrequest_active_release",
        "approvalrequest",
        ["project_key", "release_version"],
        unique=True,
        sqlite_where=sqlalchemy.text("status IN ('PENDING', 'APPROVED') AND release_version IS NOT NULL"),
    )
