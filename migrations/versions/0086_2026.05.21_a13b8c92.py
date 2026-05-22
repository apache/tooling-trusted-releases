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

"""Release inactivity tracking

Revision ID: 0086_2026.05.21_a13b8c92
Revises: 0085_2025.05.19_9d086f7e
Create Date: 2026-05-21 00:00:00.000000+00:00
"""

import datetime
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0086_2026.05.21_a13b8c92"
down_revision: str | None = "0085_2025.05.19_9d086f7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    backfill = datetime.datetime.now(datetime.UTC)

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "activity_at",
                sql.UTCDateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch_op.add_column(sa.Column("inactivity_notice_key", sa.String(), nullable=True))

    op.execute(sa.text("UPDATE release SET activity_at = :ts").bindparams(ts=backfill))

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.alter_column("activity_at", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("inactivity_notice_key")
        batch_op.drop_column("activity_at")
