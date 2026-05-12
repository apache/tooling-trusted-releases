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

"""Rename the check result taxonomy

Revision ID: 0078_2026.05.12_7ef26ec5
Revises: 0077_2026.05.08_7c3df184
Create Date: 2026-05-12 15:16:35.015248+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078_2026.05.12_7ef26ec5"
down_revision: str | None = "0077_2026.05.08_7c3df184"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATUS = sa.Enum("BLOCKER", "EXCEPTION", "FAILURE", "SUCCESS", "WARNING", name="checkresultstatus")
_NEW_STATUS = sa.Enum("BLOCKER", "CONCERN", "EXCEPTION", "NOTE", "SUGGESTION", name="checkresultstatus")
_OLD_IGNORE = sa.Enum("EXCEPTION", "FAILURE", "WARNING", name="checkresultstatusignore")
_NEW_IGNORE = sa.Enum("CONCERN", "EXCEPTION", "SUGGESTION", name="checkresultstatusignore")


def upgrade() -> None:
    op.execute("UPDATE checkresultignore SET status = NULL WHERE status = ''")
    op.execute("UPDATE checkresult SET status = 'NOTE' WHERE status IN ('SUCCESS', 'success')")
    op.execute("UPDATE checkresult SET status = 'CONCERN' WHERE status IN ('FAILURE', 'failure')")
    op.execute("UPDATE checkresult SET status = 'SUGGESTION' WHERE status IN ('WARNING', 'warning')")
    op.execute("UPDATE checkresultignore SET status = 'CONCERN' WHERE status IN ('FAILURE', 'failure')")
    op.execute("UPDATE checkresultignore SET status = 'SUGGESTION' WHERE status IN ('WARNING', 'warning')")

    with op.batch_alter_table("checkresult", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_OLD_STATUS,
            type_=_NEW_STATUS,
            existing_nullable=False,
        )
    with op.batch_alter_table("checkresultignore", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_OLD_IGNORE,
            type_=_NEW_IGNORE,
            existing_nullable=True,
        )


def downgrade() -> None:
    op.execute("UPDATE checkresult SET status = 'SUCCESS' WHERE status IN ('NOTE', 'note')")
    op.execute("UPDATE checkresult SET status = 'FAILURE' WHERE status IN ('CONCERN', 'concern')")
    op.execute("UPDATE checkresult SET status = 'WARNING' WHERE status IN ('SUGGESTION', 'suggestion')")
    op.execute("UPDATE checkresultignore SET status = 'FAILURE' WHERE status IN ('CONCERN', 'concern')")
    op.execute("UPDATE checkresultignore SET status = 'WARNING' WHERE status IN ('SUGGESTION', 'suggestion')")

    with op.batch_alter_table("checkresultignore", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_NEW_IGNORE,
            type_=_OLD_IGNORE,
            existing_nullable=True,
        )
    with op.batch_alter_table("checkresult", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_NEW_STATUS,
            type_=_OLD_STATUS,
            existing_nullable=False,
        )
