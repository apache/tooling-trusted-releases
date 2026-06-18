"""Rename usersession committees to member_committees and projects to participant_committees

Revision ID: 0095_2026.06.18_0d079d90
Revises: 0094_2026.06.10_ee041532
Create Date: 2026-06-18 17:58:23.715615
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0095_2026.06.18_0d079d90"
down_revision: str | None = "0094_2026.06.10_ee041532"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("usersession", schema=None) as batch_op:
        batch_op.alter_column("committees", new_column_name="member_committees")
        batch_op.alter_column("projects", new_column_name="participant_committees")


def downgrade() -> None:
    with op.batch_alter_table("usersession", schema=None) as batch_op:
        batch_op.alter_column("member_committees", new_column_name="committees")
        batch_op.alter_column("participant_committees", new_column_name="projects")
