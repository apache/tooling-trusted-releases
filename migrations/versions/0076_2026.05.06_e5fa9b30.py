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

"""Add LifecycleEvent for issue 914

Revision ID: 0076_2026.05.06_e5fa9b30
Revises: 0075_2026.05.04_97e3037f
Create Date: 2026-05-06 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0076_2026.05.06_e5fa9b30"
down_revision: str | None = "0075_2026.05.04_97e3037f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lifecycleevent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_key", sa.String(), nullable=False),
        sa.Column("cycle_key", sa.String(), nullable=True),
        sa.Column("version_key", sa.String(), nullable=True),
        sa.Column(
            "event",
            sa.Enum("RELEASE", "ARCHIVE", "WITHDRAW", "EOD", "EOS", "EOL", name="lifecycleeventtype"),
            nullable=False,
        ),
        sa.Column("effective", sql.UTCDateTime(timezone=True), nullable=False),
        sa.Column("published", sql.UTCDateTime(timezone=True), nullable=False),
        sa.Column("target_event_id", sa.Integer(), nullable=True),
        sa.Column("reference_urls", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_key"],
            ["project.key"],
            name=op.f("fk_lifecycleevent_project_key_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_key"],
            ["projectcycle.cycle_key"],
            name=op.f("fk_lifecycleevent_cycle_key_projectcycle"),
        ),
        sa.ForeignKeyConstraint(
            ["version_key"],
            ["release.key"],
            name=op.f("fk_lifecycleevent_version_key_release"),
        ),
        sa.ForeignKeyConstraint(
            ["target_event_id"],
            ["lifecycleevent.id"],
            name=op.f("fk_lifecycleevent_target_event_id_lifecycleevent"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycleevent")),
    )
    with op.batch_alter_table("lifecycleevent", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_lifecycleevent_target_event_id"), ["target_event_id"], unique=False)
        batch_op.create_index("ix_lifecycleevent_project_event", ["project_key", "event"], unique=False)
        batch_op.create_index("ix_lifecycleevent_cycle_event", ["cycle_key", "event"], unique=False)
        batch_op.create_index("ix_lifecycleevent_version_event", ["version_key", "event"], unique=False)


def downgrade() -> None:
    op.drop_table("lifecycleevent")
