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

"""Repair databases that ran the original form of 0101

Revision ID: 0103_2026.07.02_18eb7fcf
Revises: 0102_2026.06.30_258b186f
Create Date: 2026-07-02 18:26:17.878182+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0103_2026.07.02_18eb7fcf"
down_revision: str | None = "0102_2026.06.30_258b186f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Migration 0101 originally added artifact.committee_key, and was then rewritten in place to
# widen download_path_suffix instead. A database which ran the original form has the stray
# column, and never ran the widening UPDATE. Such a database cannot contain widened rows,
# because code carrying the rewritten 0101 refuses to start against it, so the widening is
# owed if and only if the stray column is present
_WIDEN_DOWNLOAD_PATH_SUFFIX = """
    UPDATE artifact
    SET download_path_suffix = (
            SELECT (CASE WHEN c.is_podling THEN 'incubator/' ELSE '' END) || c.key
            FROM project p
            JOIN committee c ON c.key = p.committee_key
            WHERE p.key = artifact.project_key
        )
        || (CASE WHEN COALESCE(download_path_suffix, '') = '' THEN '' ELSE '/' || download_path_suffix END)
    WHERE EXISTS (
        SELECT 1 FROM project p
        JOIN committee c ON c.key = p.committee_key
        WHERE p.key = artifact.project_key
    )
"""


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("artifact")}
    if "committee_key" not in columns:
        return
    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.drop_column("committee_key")
    op.execute(_WIDEN_DOWNLOAD_PATH_SUFFIX)


def downgrade() -> None:
    # The repair is conditional on database history and the widening doesn't reverse
    pass
