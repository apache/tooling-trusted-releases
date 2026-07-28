# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Add the BROKEN task status for retryable failures

Revision ID: 0113_2026.07.28_dd069840
Revises: 0112_2026.07.22_d374db02
Create Date: 2026-07-28 13:34:48.150920+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0113_2026.07.28_dd069840"
down_revision: str | None = "0112_2026.07.22_d374db02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_WITHOUT_BROKEN = """
            (
                -- Initial state is always valid
                status = 'QUEUED'
                -- QUEUED -> ACTIVE requires setting started time and pid
                OR (status = 'ACTIVE' AND started IS NOT NULL AND pid IS NOT NULL)
                -- ACTIVE -> COMPLETED requires setting completed time and result
                OR (status = 'COMPLETED' AND completed IS NOT NULL AND result IS NOT NULL)
                -- ACTIVE -> FAILED requires setting completed time and error (result optional)
                OR (status = 'FAILED' AND completed IS NOT NULL AND error IS NOT NULL)
            )
            """

_CHECK_WITH_BROKEN = """
            (
                -- Initial state is always valid
                status = 'QUEUED'
                -- QUEUED -> ACTIVE requires setting started time and pid
                OR (status = 'ACTIVE' AND started IS NOT NULL AND pid IS NOT NULL)
                -- ACTIVE -> COMPLETED requires setting completed time and result
                OR (status = 'COMPLETED' AND completed IS NOT NULL AND result IS NOT NULL)
                -- ACTIVE -> FAILED requires setting completed time and error (result optional)
                OR (status = 'FAILED' AND completed IS NOT NULL AND error IS NOT NULL)
                -- ACTIVE -> BROKEN requires setting completed time and error (result optional)
                OR (status = 'BROKEN' AND completed IS NOT NULL AND error IS NOT NULL)
            )
            """


def upgrade() -> None:
    op.execute(
        "UPDATE task SET completed = COALESCE(completed, started, added)"
        " WHERE status IN ('COMPLETED', 'FAILED') AND completed IS NULL"
    )
    op.execute("UPDATE task SET result = 'null' WHERE status = 'COMPLETED' AND result IS NULL")
    op.execute("UPDATE task SET error = 'unknown error' WHERE status = 'FAILED' AND error IS NULL")
    op.execute(
        "UPDATE task SET status = 'FAILED', completed = COALESCE(completed, started, added),"
        " error = 'Task was active with no recorded worker'"
        " WHERE status = 'ACTIVE' AND (started IS NULL OR pid IS NULL)"
    )
    with op.batch_alter_table("task", schema=None, copy_from=_task_table()) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_task_valid_task_status_transitions"),
            _CHECK_WITH_BROKEN,
        )


def downgrade() -> None:
    op.execute("UPDATE task SET status = 'FAILED' WHERE status = 'BROKEN'")
    with op.batch_alter_table("task", schema=None, copy_from=_task_table()) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_task_valid_task_status_transitions"),
            _CHECK_WITHOUT_BROKEN,
        )


def _task_table() -> sa.Table:
    return sa.Table(
        "task",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(9), nullable=False),
        sa.Column("task_type", sa.String(23), nullable=False),
        sa.Column("task_args", sa.JSON(), nullable=True),
        sa.Column("added", sa.TIMESTAMP(), nullable=False),
        sa.Column("started", sa.TIMESTAMP(), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("completed", sa.TIMESTAMP(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("revision_number", sa.String(), nullable=True),
        sa.Column("primary_rel_path", sa.String(), nullable=True),
        sa.Column("asf_uid", sa.String(), server_default=sa.text("('')"), nullable=False),
        sa.Column("scheduled", sa.TIMESTAMP(), nullable=True),
        sa.Column("inputs_hash", sa.String(), nullable=True),
        sa.Column("project_key", sa.String(), nullable=True),
        sa.Column("version_key", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_task"),
        sa.ForeignKeyConstraint(["project_key"], ["project.key"], name="fk_task_project_key_project"),
        sa.Index("ix_task_added", "added"),
        sa.Index("ix_task_inputs_hash", "inputs_hash", unique=True),
        sa.Index("ix_task_primary_rel_path", "primary_rel_path"),
        sa.Index("ix_task_revision_number", "revision_number"),
        sa.Index("ix_task_scheduled", "scheduled"),
        sa.Index("ix_task_status", "status"),
        sa.Index("ix_task_status_added", "status", "added"),
        sa.Index("ix_task_version_key", "version_key"),
    )
