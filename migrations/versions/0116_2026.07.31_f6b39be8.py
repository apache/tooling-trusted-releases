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

"""Add the vote thread URL to releases

Revision ID: 0116_2026.07.31_f6b39be8
Revises: 0115_2026.07.31_7169d961
Create Date: 2026-07-31 15:18:34.624854+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0116_2026.07.31_f6b39be8"
down_revision: str | None = "0115_2026.07.31_7169d961"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("vote_thread_url", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("vote_thread_url")
