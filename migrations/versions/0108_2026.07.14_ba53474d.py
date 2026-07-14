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

"""Add link to notifications

Revision ID: 0108_2026.07.14_ba53474d
Revises: 0107_2026.07.14_cb5f8249
Create Date: 2026-07-14 15:26:32.523085+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0108_2026.07.14_ba53474d"
down_revision: str | None = "0107_2026.07.14_cb5f8249"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notification", schema=None) as batch_op:
        batch_op.add_column(sa.Column("link", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("link_text", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("notification", schema=None) as batch_op:
        batch_op.drop_column("link_text")
        batch_op.drop_column("link")
