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

"""Add a project-configured .rat-excludes URL

Revision ID: 0125_2026.09.08_fbcb11d5
Revises: 0124_2026.09.02_b7e2f1a9
Create Date: 2026-09-08 11:13:01.671681+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0125_2026.09.08_fbcb11d5"
down_revision: str | None = "0124_2026.09.02_b7e2f1a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rat_excludes_url", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("rat_excludes_url")
