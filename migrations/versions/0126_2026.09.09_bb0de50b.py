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

"""Add cascade delete to KeyLink on committee

Revision ID: 0126_2026.09.09_bb0de50b
Revises: 0125_2026.09.08_fbcb11d5
Create Date: 2026-09-09 10:41:17.191167+00:00
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0126_2026.09.09_bb0de50b"
down_revision: str | None = "0125_2026.09.08_fbcb11d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("keylink", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_keylink_committee_key_committee"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_keylink_committee_key_committee"),
            "committee",
            ["committee_key"],
            ["key"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("keylink", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_keylink_committee_key_committee"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_keylink_committee_key_committee"), "committee", ["committee_key"], ["key"]
        )
