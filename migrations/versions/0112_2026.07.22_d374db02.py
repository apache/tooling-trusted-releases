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

"""Rename the DISCLAIMER announce template variable

Revision ID: 0112_2026.07.22_d374db02
Revises: 0111_2026.07.22_f67fc10f
Create Date: 2026-07-22 20:01:34.682465+00:00
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0112_2026.07.22_d374db02"
down_revision: str | None = "0111_2026.07.22_f67fc10f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE releasepolicy SET announce_release_template ="
        " REPLACE(announce_release_template, '{{DISCLAIMER}}', '{{PODLING_DISCLAIMER}}')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE releasepolicy SET announce_release_template ="
        " REPLACE(announce_release_template, '{{PODLING_DISCLAIMER}}', '{{DISCLAIMER}}')"
    )
