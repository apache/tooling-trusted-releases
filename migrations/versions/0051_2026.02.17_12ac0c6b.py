"""Unique index

Revision ID: 0051_2026.02.17_12ac0c6b
Revises: 0050_2026.02.17_7406bb29
Create Date: 2026-02-17 16:46:37.248657+00:00
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0051_2026.02.17_12ac0c6b"
down_revision: str | None = "0050_2026.02.17_7406bb29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_task_inputs_hash"))
        batch_op.create_index(batch_op.f("ix_task_inputs_hash"), ["inputs_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_task_inputs_hash"))
        batch_op.create_index(batch_op.f("ix_task_inputs_hash"), ["inputs_hash"], unique=False)
