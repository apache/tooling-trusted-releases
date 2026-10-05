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

"""Add email template URLs to the release policy

Revision ID: 0134_2026.10.05_a3c91e47
Revises: 0133_2026.10.02_5bb098dd
Create Date: 2026-10-05 09:12:44.318201+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0134_2026.10.05_a3c91e47"
down_revision: str | None = "0133_2026.10.02_5bb098dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[str, ...] = (
    "vote_comment_template_url",
    "start_vote_template_url",
    "finish_vote_template_url",
    "announce_release_template_url",
)


def upgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        for column in _COLUMNS:
            batch_op.add_column(sa.Column(column, sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        for column in _COLUMNS:
            batch_op.drop_column(column)
