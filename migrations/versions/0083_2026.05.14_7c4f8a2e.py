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

"""Add Artifact catalog model for issue 911

Revision ID: 0083_2026.05.14_7c4f8a2e
Revises: 0082_2026.05.14_a1b2c3d4
Create Date: 2026-05-14 16:01:57.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0083_2026.05.14_7c4f8a2e"
down_revision: str | None = "0082_2026.05.14_a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact",
        sa.Column("project_key", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("artifact_path", sa.String(), nullable=False),
        sa.Column("release_key", sa.String(), nullable=True),
        sa.Column("key_fingerprint", sa.String(), nullable=True),
        sa.Column("signature_path", sa.String(), nullable=True),
        sa.Column("checksum_path", sa.String(), nullable=True),
        sa.Column("classification", sa.String(), nullable=True),
        sa.Column("svn_revision", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_key"],
            ["project.key"],
            name=op.f("fk_artifact_project_key_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["release_key"],
            ["release.key"],
            name=op.f("fk_artifact_release_key_release"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["key_fingerprint"],
            ["publicsigningkey.fingerprint"],
            name=op.f("fk_artifact_key_fingerprint_publicsigningkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("project_key", "version", "artifact_path", name=op.f("pk_artifact")),
    )
    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_artifact_release_key"), ["release_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_artifact_key_fingerprint"), ["key_fingerprint"], unique=False)
        batch_op.create_index(batch_op.f("ix_artifact_svn_revision"), ["svn_revision"], unique=False)


def downgrade() -> None:
    op.drop_table("artifact")
