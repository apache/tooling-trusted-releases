"""Add index on checkresult.release_name

Revision ID: 0036_2026.01.12_3831f215
Revises: 0035_2026.01.08_2bbfd636
Create Date: 2026-01-12 20:13:19.789567+00:00
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0036_2026.01.12_3831f215"
down_revision: str | None = "0035_2026.01.08_2bbfd636"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("checkresult", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_checkresult_release_name"), ["release_name"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("checkresult", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_checkresult_release_name"))
