"""Move the vote mode from projects to committees

Revision ID: 0150_2026.11.24_386450a3
Revises: 0149_2026.11.19_bc613d78
Create Date: 2026-11-24 14:02:44.581272+00:00
"""

import collections.abc as abc
from typing import Final

import alembic.op as op
import sqlalchemy

revision: str = "0150_2026.11.24_386450a3"
down_revision: str | None = "0149_2026.11.19_bc613d78"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None

_ACTION_ENUM: Final = sqlalchemy.Enum(
    "ARCHIVE", "DELETE", "EMAIL_VOTING", "TRUSTED_VOTING", "UNARCHIVE", name="approvalaction"
)
_ACTION_ENUM_PRIOR: Final = sqlalchemy.Enum("ARCHIVE", "DELETE", "UNARCHIVE", name="approvalaction")

_KEEP_TRUSTED_COMMITTEES_TRUSTED: Final = sqlalchemy.text("""
    UPDATE committee SET vote_mode = 'TRUSTED'
    WHERE committee.is_podling = 0
    AND EXISTS (
        SELECT 1 FROM project
        JOIN releasepolicy ON releasepolicy.id = project.release_policy_id
        WHERE project.committee_key = committee.key
        AND releasepolicy.vote_mode = 'TRUSTED'
    )
""")

_RESTORE_PROJECT_TRUSTED_VOTES: Final = sqlalchemy.text("""
    UPDATE releasepolicy SET vote_mode = 'TRUSTED'
    WHERE id IN (
        SELECT project.release_policy_id FROM project
        JOIN committee ON committee.key = project.committee_key
        WHERE committee.vote_mode = 'TRUSTED'
    )
""")


def downgrade() -> None:
    active_id = op.get_bind().scalar(
        sqlalchemy.text(
            "SELECT id FROM approvalrequest"
            " WHERE action IN ('EMAIL_VOTING', 'TRUSTED_VOTING') AND status IN ('PENDING', 'APPROVED') LIMIT 1"
        )
    )
    if active_id is not None:
        raise RuntimeError(f"Vote mode approval request {active_id} is still active; resolve it before migrating")
    op.execute(sqlalchemy.text("DELETE FROM approvalrequest WHERE action IN ('EMAIL_VOTING', 'TRUSTED_VOTING')"))

    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(
            sqlalchemy.Column(
                "vote_mode", sqlalchemy.VARCHAR(length=7), server_default=sqlalchemy.text("'EMAIL'"), nullable=False
            )
        )
    op.execute(_RESTORE_PROJECT_TRUSTED_VOTES)

    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.drop_column("vote_mode")

    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.alter_column("action", existing_type=_ACTION_ENUM, type_=_ACTION_ENUM_PRIOR, existing_nullable=False)
        batch_op.alter_column("cap_question_id", existing_type=sqlalchemy.INTEGER(), nullable=False)
        batch_op.alter_column("project_key", existing_type=sqlalchemy.VARCHAR(), nullable=False)


def upgrade() -> None:
    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.alter_column("project_key", existing_type=sqlalchemy.VARCHAR(), nullable=True)
        batch_op.alter_column("action", existing_type=_ACTION_ENUM_PRIOR, type_=_ACTION_ENUM, existing_nullable=False)
        batch_op.alter_column("cap_question_id", existing_type=sqlalchemy.INTEGER(), nullable=True)

    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(
            sqlalchemy.Column("vote_mode", sqlalchemy.Enum("EMAIL", "TRUSTED", name="votemode"), nullable=True)
        )
    op.execute(_KEEP_TRUSTED_COMMITTEES_TRUSTED)

    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("vote_mode")
