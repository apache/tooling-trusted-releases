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

"""Add approvalrequest table for CAP gated archival and deletion

Revision ID: 0102_2026.06.30_258b186f
Revises: 0101_2026.06.30_ee33c8d8
Create Date: 2026-06-30 17:59:31.689612+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

revision: str = "0102_2026.06.30_258b186f"
down_revision: str | None = "0101_2026.06.30_ee33c8d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvalrequest",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_key", sa.String(), nullable=False),
        sa.Column("committee_key", sa.String(), nullable=False),
        sa.Column("action", sa.Enum("ARCHIVE", "DELETE", name="approvalaction"), nullable=False),
        sa.Column("cap_question_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "APPROVED", "REJECTED", "COMPLETED", "FAILED", name="approvalstatus"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("requested_at", sql.UTCDateTime(timezone=True), nullable=False),
        sa.Column("closes_at", sql.UTCDateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sql.UTCDateTime(timezone=True), nullable=True),
        sa.Column("permalink", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvalrequest")),
        sa.UniqueConstraint("cap_question_id", name=op.f("uq_approvalrequest_cap_question_id")),
    )
    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_approvalrequest_status"), ["status"], unique=False)
        batch_op.create_index(
            "ix_approvalrequest_active_project",
            ["project_key"],
            unique=True,
            sqlite_where=sa.text("status IN ('PENDING', 'APPROVED')"),
        )


def downgrade() -> None:
    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.drop_index("ix_approvalrequest_active_project")
        batch_op.drop_index(batch_op.f("ix_approvalrequest_status"))
    op.drop_table("approvalrequest")
