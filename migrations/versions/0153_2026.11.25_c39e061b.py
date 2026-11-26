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

"""Add cycle overrides, the release settings that differ for one cycle of a project

Revision ID: 0153_2026.11.25_c39e061b
Revises: 0152_2026.11.26_6770c14a
Create Date: 2026-11-25 11:58:46.487844+00:00
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0153_2026.11.25_c39e061b"
down_revision: str | None = "0152_2026.11.26_6770c14a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPDATE_TYPE_ENUM: Final = sa.Enum("MANUAL", "BOOTSTRAP", "ASFYAML", "ASFYAML_FULL", name="updatetype")


def upgrade() -> None:
    op.create_table(
        "cycleoverrides",
        sa.Column("project_key", sa.String(), nullable=False),
        sa.Column("cycle", sa.String(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("updated", sql.UTCDateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("update_type", _UPDATE_TYPE_ENUM, nullable=False),
        sa.ForeignKeyConstraint(
            ["project_key"],
            ["project.key"],
            name=op.f("fk_cycleoverrides_project_key_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_key", "cycle", name=op.f("pk_cycleoverrides")),
    )
    op.create_index(op.f("ix_cycleoverrides_project_key"), "cycleoverrides", ["project_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cycleoverrides_project_key"), table_name="cycleoverrides")
    op.drop_table("cycleoverrides")
