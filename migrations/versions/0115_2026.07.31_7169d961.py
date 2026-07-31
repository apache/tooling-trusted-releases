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

"""Add a PubSub failure table

Revision ID: 0115_2026.07.31_7169d961
Revises: 0114_2026.07.29_4a3ce951
Create Date: 2026-07-31 15:16:17.560375+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0115_2026.07.31_7169d961"
down_revision: str | None = "0114_2026.07.29_4a3ce951"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pubsubfailure",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sql.UTCDateTime(timezone=True), nullable=False),
        sa.Column("cursor", sa.String(), nullable=True),
        sa.Column("detail", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pubsubfailure")),
    )


def downgrade() -> None:
    op.drop_table("pubsubfailure")
