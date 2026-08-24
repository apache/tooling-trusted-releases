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

"""Add the OpenPGP certificate log

Revision ID: 0121_2026.08.21_fdda1630
Revises: 0120_2026.08.11_b89e555b
Create Date: 2026-08-21 15:31:42.118407+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0121_2026.08.21_fdda1630"
down_revision: str | None = "0120_2026.08.11_b89e555b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "keyattestable",
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("operation", sa.Enum("delete", "restore", "revise", name="keyoperation"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("input", sa.LargeBinary(), nullable=True),
        sa.Column("deletions", sa.LargeBinary(), nullable=True),
        sa.Column("additions", sa.LargeBinary(), nullable=True),
        sa.Column("updated", sql.UTCDateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("role", sa.Enum("admin", "service", "user", name="keyrole"), nullable=False),
        sa.PrimaryKeyConstraint("fingerprint", "seq", name=op.f("pk_keyattestable")),
    )


def downgrade() -> None:
    op.drop_table("keyattestable")
