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

"""Release archive source

Revision ID: 0124_2026.09.02_b7e2f1a9
Revises: 0123_2026.08.27_853c7eed
Create Date: 2026-09-02 14:23:07.482915+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0124_2026.09.02_b7e2f1a9"
down_revision: str | None = "0123_2026.08.27_853c7eed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLModel persists enums by member name, so the column holds MANUAL/CAP/... not values.
    # Nullable: releases archived before this was tracked keep a null source.
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "archive_source",
                sa.Enum("MANUAL", "CAP", "AUTO_PRIOR", "DIST_WATCHER", "CATALOG_ADMIN", name="archivesource"),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_column("archive_source")
