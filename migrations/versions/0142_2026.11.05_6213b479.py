"""Add project unarchival approval requests

Revision ID: 0142_2026.11.05_6213b479
Revises: 0141_2026.11.04_4ccf7a32
Create Date: 2026-11-05 10:04:23.763662+00:00
"""

import collections.abc as abc

import alembic.op as op
import sqlalchemy

revision: str = "0142_2026.11.05_6213b479"
down_revision: str | None = "0141_2026.11.04_4ccf7a32"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.alter_column(
            "action",
            existing_type=sqlalchemy.Enum("ARCHIVE", "DELETE", name="approvalaction"),
            type_=sqlalchemy.Enum("ARCHIVE", "DELETE", "UNARCHIVE", name="approvalaction"),
            existing_nullable=False,
        )


def downgrade() -> None:
    active_id = op.get_bind().scalar(
        sqlalchemy.text(
            "SELECT id FROM approvalrequest WHERE action = 'UNARCHIVE' AND status IN ('PENDING', 'APPROVED') LIMIT 1"
        )
    )
    if active_id is not None:
        raise RuntimeError(f"Unarchival approval request {active_id} is still active; resolve it before migrating")
    # The unarchival requests deleted here can't be restored
    op.execute(sqlalchemy.text("DELETE FROM approvalrequest WHERE action = 'UNARCHIVE'"))

    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.alter_column(
            "action",
            existing_type=sqlalchemy.Enum("ARCHIVE", "DELETE", "UNARCHIVE", name="approvalaction"),
            type_=sqlalchemy.Enum("ARCHIVE", "DELETE", name="approvalaction"),
            existing_nullable=False,
        )
