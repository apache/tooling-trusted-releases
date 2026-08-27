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

"""KEYS management mode and reflect flag

Revision ID: 0123_2026.08.27_853c7eed
Revises: 0122_2026.08.25_76eb8b67
Create Date: 2026-08-27 10:20:21.121336+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0123_2026.08.27_853c7eed"
down_revision: str | None = "0122_2026.08.25_76eb8b67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLModel persists enums by member name, so the column holds AUTOMATIC/MANUAL/REFLECT, not values
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("keys_mode", sa.Enum("AUTOMATIC", "MANUAL", "REFLECT", name="keysmode"), nullable=True)
        )
    connection = op.get_bind()
    connection.execute(
        sa.text("UPDATE committee SET keys_mode = CASE WHEN automated_keys_file THEN 'AUTOMATIC' ELSE 'REFLECT' END")
    )
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.alter_column(
            "keys_mode",
            existing_type=sa.Enum("AUTOMATIC", "MANUAL", "REFLECT", name="keysmode"),
            nullable=False,
        )
        batch_op.drop_column("automated_keys_file")

    with op.batch_alter_table("keylink", schema=None) as batch_op:
        batch_op.add_column(sa.Column("svn_removed_flagged", sql.UTCDateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("keylink", schema=None) as batch_op:
        batch_op.drop_column("svn_removed_flagged")

    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(sa.Column("automated_keys_file", sa.Boolean(), nullable=True))
    connection = op.get_bind()
    # UPPER so this is safe even if an earlier attempt left the column holding lower-case values.
    connection.execute(sa.text("UPDATE committee SET automated_keys_file = (UPPER(keys_mode) = 'AUTOMATIC')"))
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.alter_column("automated_keys_file", existing_type=sa.Boolean(), nullable=False)
        batch_op.drop_column("keys_mode")
