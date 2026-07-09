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

"""Add consumed workflow token identifiers

Revision ID: 0105_2026.07.09_3414415d
Revises: 0104_2026.07.05_18ca75dd
Create Date: 2026-07-09 09:19:22.342949+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0105_2026.07.09_3414415d"
down_revision: str | None = "0104_2026.07.05_18ca75dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflowjti",
        sa.Column("jti", sa.String(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("jti", name=op.f("pk_workflowjti")),
    )
    with op.batch_alter_table("workflowjti", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workflowjti_expires"), ["expires"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("workflowjti", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workflowjti_expires"))

    op.drop_table("workflowjti")
