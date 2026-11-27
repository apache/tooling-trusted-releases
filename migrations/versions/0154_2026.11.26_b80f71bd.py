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

"""Add the public mailing lists and the domain each committee has its lists on

Revision ID: 0154_2026.11.26_b80f71bd
Revises: 0153_2026.11.25_c39e061b
Create Date: 2026-11-26 15:40:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0154_2026.11.26_b80f71bd"
down_revision: str | None = "0153_2026.11.25_c39e061b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mail_domain", sa.String(), nullable=True))
    op.create_table(
        "mailinglist",
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("domain", "label", name=op.f("pk_mailinglist")),
    )


def downgrade() -> None:
    op.drop_table("mailinglist")
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.drop_column("mail_domain")
