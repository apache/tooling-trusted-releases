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

"""Add key deletion and signature hints

Revision ID: 0104_2026.07.05_18ca75dd
Revises: 0103_2026.07.02_18eb7fcf
Create Date: 2026-07-05 10:37:42.059429+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0104_2026.07.05_18ca75dd"
down_revision: str | None = "0103_2026.07.02_18eb7fcf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signaturehint",
        sa.Column("hint", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("hint", name=op.f("pk_signaturehint")),
    )
    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_artifact_key_fingerprint_publicsigningkey"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_artifact_key_fingerprint_publicsigningkey"),
            "publicsigningkey",
            ["key_fingerprint"],
            ["fingerprint"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("publicsigningkey", schema=None) as batch_op:
        batch_op.add_column(sa.Column("deleted", sql.UTCDateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("historic_use", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("publicsigningkey", schema=None) as batch_op:
        batch_op.drop_column("historic_use")
        batch_op.drop_column("deleted")

    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_artifact_key_fingerprint_publicsigningkey"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_artifact_key_fingerprint_publicsigningkey"),
            "publicsigningkey",
            ["key_fingerprint"],
            ["fingerprint"],
            ondelete="SET NULL",
        )

    op.drop_table("signaturehint")
