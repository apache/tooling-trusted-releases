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

"""Deduplicate notifications

Revision ID: 0122_2026.08.25_76eb8b67
Revises: 0121_2026.08.21_fdda1630
Create Date: 2026-08-25 13:46:55.901179+00:00
"""

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0122_2026.08.25_76eb8b67"
down_revision: str | None = "0121_2026.08.21_fdda1630"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notification", schema=None) as batch_op:
        batch_op.add_column(sa.Column("dedup_hash", sa.String(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, level, message, link, link_text FROM notification")).fetchall()
    for row in rows:
        encoded = json.dumps([row.level.lower(), row.message, row.link, row.link_text])
        connection.execute(
            sa.text("UPDATE notification SET dedup_hash = :dedup_hash WHERE id = :id"),
            {"dedup_hash": hashlib.sha3_256(encoded.encode()).hexdigest(), "id": row.id},
        )
    connection.execute(
        sa.text(
            "DELETE FROM notification WHERE id NOT IN (SELECT MIN(id) FROM notification GROUP BY asf_uid, dedup_hash)"
        )
    )

    with op.batch_alter_table("notification", schema=None) as batch_op:
        batch_op.alter_column("dedup_hash", existing_type=sa.String(), nullable=False)
        batch_op.create_index("ix_notification_asf_uid_dedup_hash", ["asf_uid", "dedup_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("notification", schema=None) as batch_op:
        batch_op.drop_index("ix_notification_asf_uid_dedup_hash")
        batch_op.drop_column("dedup_hash")
