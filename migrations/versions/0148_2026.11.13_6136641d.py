"""Return projects given trusted votes by a full sync to email votes

Revision ID: 0148_2026.11.13_6136641d
Revises: 0147_2026.11.13_bb0b30bf
Create Date: 2026-11-13 16:38:17.875296+00:00
"""

import collections.abc as abc
from typing import Final

import alembic.op as op
import sqlalchemy

revision: str = "0148_2026.11.13_6136641d"
down_revision: str | None = "0147_2026.11.13_bb0b30bf"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None

_RETURN_FULL_SYNC_PROJECTS_TO_EMAIL: Final = sqlalchemy.text("""
    UPDATE releasepolicy SET vote_mode = 'EMAIL'
    WHERE vote_mode = 'TRUSTED'
    AND id IN (SELECT release_policy_id FROM project WHERE update_type = 'ASFYAML_FULL')
""")


def downgrade() -> None:
    pass


def upgrade() -> None:
    op.execute(_RETURN_FULL_SYNC_PROJECTS_TO_EMAIL)
