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

"""Add expected distributions and a status for recorded ones

Revision ID: 0149_2026.11.19_bc613d78
Revises: 0148_2026.11.13_6136641d
Create Date: 2026-11-19 13:16:48.353440+00:00
"""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0149_2026.11.19_bc613d78"
down_revision: str | None = "0148_2026.11.13_6136641d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLATFORM_ENUM: Final = sa.Enum(
    "ARTIFACT_HUB", "DOCKER_HUB", "MAVEN", "NPM", "NPM_SCOPED", "PYPI", name="distributionplatform"
)
_STATUS_ENUM: Final = sa.Enum("CHECKING", "FOUND", "IGNORED", name="distributionstatus")


def upgrade() -> None:
    op.create_table(
        "expecteddistribution",
        sa.Column("project_key", sa.String(), nullable=False),
        sa.Column("platform", _PLATFORM_ENUM, nullable=False),
        sa.Column("owner_namespace", sa.String(), nullable=False),
        sa.Column("package", sa.String(), nullable=False),
        sa.Column("version_pattern", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_key"],
            ["project.key"],
            name=op.f("fk_expecteddistribution_project_key_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "project_key",
            "platform",
            "owner_namespace",
            "package",
            "version_pattern",
            name=op.f("pk_expecteddistribution"),
        ),
    )
    op.create_index(op.f("ix_expecteddistribution_project_key"), "expecteddistribution", ["project_key"], unique=False)

    with op.batch_alter_table("distribution", schema=None) as batch_op:
        batch_op.add_column(sa.Column("status", _STATUS_ENUM, server_default="FOUND", nullable=False))
        batch_op.add_column(sa.Column("last_checked", sql.UTCDateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("ignored_by", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("ignored_reason", sa.String(), nullable=True))
    op.execute("UPDATE distribution SET status = 'CHECKING' WHERE pending = 1")
    with op.batch_alter_table("distribution", schema=None) as batch_op:
        batch_op.alter_column("status", server_default=None)
        batch_op.drop_column("pending")


def downgrade() -> None:
    with op.batch_alter_table("distribution", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pending", sa.Boolean(), server_default=sa.text("0"), nullable=False))
    # An ignored row has no equivalent, and would read as a found one, so it's removed
    op.execute("DELETE FROM distribution WHERE UPPER(status) = 'IGNORED'")
    op.execute("UPDATE distribution SET pending = 1 WHERE UPPER(status) = 'CHECKING'")
    with op.batch_alter_table("distribution", schema=None) as batch_op:
        batch_op.drop_column("ignored_reason")
        batch_op.drop_column("ignored_by")
        batch_op.drop_column("last_checked")
        batch_op.drop_column("status")

    op.drop_index(op.f("ix_expecteddistribution_project_key"), table_name="expecteddistribution")
    op.drop_table("expecteddistribution")
