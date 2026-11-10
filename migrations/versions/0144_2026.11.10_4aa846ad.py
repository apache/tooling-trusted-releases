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

"""Add project JWTs issued to workflows

Revision ID: 0144_2026.11.10_4aa846ad
Revises: 0143_2026.11.05_71710c28
Create Date: 2026-11-10 14:28:15.689710+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0144_2026.11.10_4aa846ad"
down_revision: str | None = "0143_2026.11.05_71710c28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflowjwt",
        sa.Column("jti", sa.String(), nullable=False),
        sa.Column("project_key", sa.String(), nullable=False),
        sa.Column("asf_uid", sa.String(), nullable=False),
        sa.Column("github_uid", sa.String(), nullable=False),
        sa.Column("github_nid", sa.Integer(), nullable=False),
        sa.Column("github_payload", sa.JSON(), nullable=False),
        sa.Column("source_commit", sa.String(), nullable=True),
        sa.Column("declare_sources", sa.Boolean(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("jti", name=op.f("pk_workflowjwt")),
    )
    with op.batch_alter_table("workflowjwt", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workflowjwt_asf_uid"), ["asf_uid"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflowjwt_expires"), ["expires"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflowjwt_project_key"), ["project_key"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("workflowjwt", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workflowjwt_project_key"))
        batch_op.drop_index(batch_op.f("ix_workflowjwt_expires"))
        batch_op.drop_index(batch_op.f("ix_workflowjwt_asf_uid"))

    op.drop_table("workflowjwt")
