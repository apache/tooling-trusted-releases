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

"""Cascade lifecycleevent deletion on release deletion

Revision ID: 0079_2026.05.12_c9f4a1d2
Revises: 0078_2026.05.12_7ef26ec5
Create Date: 2026-05-12 18:13:10.719361+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0079_2026.05.12_c9f4a1d2"
down_revision: str | None = "0078_2026.05.12_7ef26ec5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s"}
_VERSION_KEY_FK = "fk_lifecycleevent_version_key_release"


def upgrade() -> None:
    with op.batch_alter_table("lifecycleevent", schema=None, naming_convention=_NAMING_CONVENTION) as batch_op:
        batch_op.drop_constraint(batch_op.f(_VERSION_KEY_FK), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f(_VERSION_KEY_FK),
            "release",
            ["version_key"],
            ["key"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("lifecycleevent", schema=None, naming_convention=_NAMING_CONVENTION) as batch_op:
        batch_op.drop_constraint(batch_op.f(_VERSION_KEY_FK), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f(_VERSION_KEY_FK),
            "release",
            ["version_key"],
            ["key"],
        )
