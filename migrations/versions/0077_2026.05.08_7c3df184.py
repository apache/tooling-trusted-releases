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

"""Add Project reference metadata columns

Revision ID: 0077_2026.05.08_7c3df184
Revises: 0076_2026.05.06_e5fa9b30
Create Date: 2026-05-08 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0077_2026.05.08_7c3df184"
down_revision: str | None = "0076_2026.05.06_e5fa9b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sa.Column("short_description", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("homepage", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("lifecycle_page", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("download_page", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("bug_database", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("mailing_lists", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("repository", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("standards", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("standards")
        batch_op.drop_column("repository")
        batch_op.drop_column("mailing_lists")
        batch_op.drop_column("bug_database")
        batch_op.drop_column("download_page")
        batch_op.drop_column("lifecycle_page")
        batch_op.drop_column("homepage")
        batch_op.drop_column("short_description")
