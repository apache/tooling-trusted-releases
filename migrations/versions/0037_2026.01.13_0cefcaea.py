"""Add status for external workflows

Revision ID: 0037_2026.01.13_0cefcaea
Revises: 0036_2026.01.12_3831f215
Create Date: 2026-01-13 14:36:37.322569+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0037_2026.01.13_0cefcaea"
down_revision: str | None = "0036_2026.01.12_3831f215"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflowstatus",
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task.id"], name=op.f("fk_workflowstatus_task_id_task"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("workflow_id", "run_id", name=op.f("pk_workflowstatus")),
    )
    with op.batch_alter_table("workflowstatus", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workflowstatus_project_name"), ["project_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflowstatus_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflowstatus_workflow_id"), ["workflow_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("workflowstatus", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workflowstatus_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_workflowstatus_run_id"))
        batch_op.drop_index(batch_op.f("ix_workflowstatus_project_name"))

    op.drop_table("workflowstatus")
