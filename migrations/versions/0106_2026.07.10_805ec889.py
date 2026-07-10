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

"""Add site banner

Revision ID: 0106_2026.07.10_805ec889
Revises: 0105_2026.07.09_3414415d
Create Date: 2026-07-10 13:16:33.044174+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0106_2026.07.10_805ec889"
down_revision: str | None = "0105_2026.07.09_3414415d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "banner",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("markdown", sa.String(), nullable=False),
        sa.Column("asf_uid", sa.String(), nullable=False),
        sa.Column("set_at", sql.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_banner")),
    )


def downgrade() -> None:
    op.drop_table("banner")
