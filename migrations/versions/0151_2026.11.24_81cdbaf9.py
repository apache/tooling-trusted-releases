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

"""Add the committee opt-in for uploads from GitHub Actions, and project security options

Revision ID: 0151_2026.11.24_81cdbaf9
Revises: 0150_2026.11.24_386450a3
Create Date: 2026-11-24 12:36:42.599938+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0151_2026.11.24_81cdbaf9"
down_revision: str | None = "0150_2026.11.24_386450a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(sa.Column("github_oidc_enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False))
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.alter_column("github_oidc_enabled", server_default=None)
    op.create_table(
        "projectsecurityoptions",
        sa.Column("project_key", sa.String(), nullable=False),
        sa.Column("github_upload_workflow_paths", sa.JSON(), nullable=False),
        sa.Column("github_upload_ref_type", sa.String(), nullable=False),
        sa.Column("github_upload_ref_patterns", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_key"],
            ["project.key"],
            name=op.f("fk_projectsecurityoptions_project_key_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_key", name=op.f("pk_projectsecurityoptions")),
    )
    # A policy's compose workflow paths become the workflows that can upload to its project. The
    # branch on the policy was never checked against anything, so it is not kept
    op.execute(
        """
        INSERT INTO projectsecurityoptions
            (project_key, github_upload_workflow_paths, github_upload_ref_type, github_upload_ref_patterns)
        SELECT p.key, rp.github_compose_workflow_path, '', '[]'
        FROM project AS p JOIN releasepolicy AS rp ON rp.id = p.release_policy_id
        WHERE json_valid(rp.github_compose_workflow_path)
        AND json_type(rp.github_compose_workflow_path) = 'array'
        AND json_array_length(rp.github_compose_workflow_path) > 0
        """
    )
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("github_compose_workflow_path")
        batch_op.drop_column("github_repository_branch")


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("github_repository_branch", sa.String(), server_default="", nullable=False))
        batch_op.add_column(sa.Column("github_compose_workflow_path", sa.JSON(), server_default="[]", nullable=False))
    op.execute(
        """
        UPDATE releasepolicy SET github_compose_workflow_path = (
            SELECT pso.github_upload_workflow_paths
            FROM project AS p JOIN projectsecurityoptions AS pso ON pso.project_key = p.key
            WHERE p.release_policy_id = releasepolicy.id
        )
        WHERE id IN (
            SELECT p.release_policy_id
            FROM project AS p JOIN projectsecurityoptions AS pso ON pso.project_key = p.key
            WHERE json_array_length(pso.github_upload_workflow_paths) > 0
        )
        """
    )
    op.drop_table("projectsecurityoptions")
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.drop_column("github_oidc_enabled")
