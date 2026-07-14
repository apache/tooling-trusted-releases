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

"""Add release version to approval requests

Revision ID: 0107_2026.07.14_cb5f8249
Revises: 0106_2026.07.10_805ec889
Create Date: 2026-07-14 15:26:32.523085+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0107_2026.07.14_cb5f8249"
down_revision: str | None = "0106_2026.07.10_805ec889"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.add_column(sa.Column("release_version", sa.String(), nullable=True))
        batch_op.alter_column(
            "action",
            existing_type=sa.VARCHAR(length=7),
            type_=sa.Enum("ARCHIVE", "ARCHIVE_RELEASE", "DELETE", name="approvalaction"),
            existing_nullable=False,
        )

    op.drop_index("ix_approvalrequest_active_project", table_name="approvalrequest")
    op.create_index(
        "ix_approvalrequest_active_project",
        "approvalrequest",
        ["project_key"],
        unique=True,
        sqlite_where=sa.text("status IN ('PENDING', 'APPROVED') AND release_version IS NULL"),
    )
    op.create_index(
        "ix_approvalrequest_active_release",
        "approvalrequest",
        ["project_key", "release_version"],
        unique=True,
        sqlite_where=sa.text("status IN ('PENDING', 'APPROVED') AND release_version IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_approvalrequest_active_release", table_name="approvalrequest")
    op.drop_index("ix_approvalrequest_active_project", table_name="approvalrequest")
    op.create_index(
        "ix_approvalrequest_active_project",
        "approvalrequest",
        ["project_key"],
        unique=True,
        sqlite_where=sa.text("status IN ('PENDING', 'APPROVED')"),
    )

    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.alter_column(
            "action",
            existing_type=sa.Enum("ARCHIVE", "ARCHIVE_RELEASE", "DELETE", name="approvalaction"),
            type_=sa.VARCHAR(length=7),
            existing_nullable=False,
        )
        batch_op.drop_column("release_version")
