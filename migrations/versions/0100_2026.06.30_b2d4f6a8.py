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

"""Add security metadata to project and download path suffix to release policy

Revision ID: 0100_2026.06.30_b2d4f6a8
Revises: 0099_2026.06.29_ca1ec0de
Create Date: 2026-06-30 11:24:07.512883+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0100_2026.06.30_b2d4f6a8"
down_revision: str | None = "0099_2026.06.29_ca1ec0de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sa.Column("security_contact", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("threat_model_link", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("threat_model_src_link", sa.String(), nullable=True))
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("download_path_suffix", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("releasepolicy", schema=None) as batch_op:
        batch_op.drop_column("download_path_suffix")
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("threat_model_src_link")
        batch_op.drop_column("threat_model_link")
        batch_op.drop_column("security_contact")
