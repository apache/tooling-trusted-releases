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

"""Add gate configurations

Revision ID: 0139_2026.10.30_24a9f908
Revises: 0138_2026.10.29_b79a1cd3
Create Date: 2026-10-30 12:34:23.846560+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0139_2026.10.30_24a9f908"
down_revision: str | None = "0138_2026.10.29_b79a1cd3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gateconfig",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("repository", sa.String(), nullable=False),
        sa.Column("committee_key", sa.String(), nullable=False),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("workflow", sa.String(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("ref_type", sa.String(), nullable=False),
        sa.Column("ref_pattern", sa.String(), nullable=False),
        sa.Column("previous_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.Enum("PENDING", "APPROVED", "REJECTED", name="gatestatus"), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("decided", sql.UTCDateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gateconfig")),
    )


def downgrade() -> None:
    op.drop_table("gateconfig")
