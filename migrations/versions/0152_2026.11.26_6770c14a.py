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

"""Unify voting modes and evolve vote interpretations into confirmed email votes

Revision ID: 0152_2026.11.26_6770c14a
Revises: 0151_2026.11.24_81cdbaf9
Create Date: 2026-11-26 09:30:00.000000+00:00
"""

from collections.abc import Sequence
from typing import Final

import alembic.op as op
import sqlalchemy as sa

import atr.models.sql as sql

revision: str = "0152_2026.11.26_6770c14a"
down_revision: str | None = "0151_2026.11.24_81cdbaf9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTION_ENUM_PRIOR: Final = sa.Enum(
    "ARCHIVE", "DELETE", "EMAIL_VOTING", "TRUSTED_VOTING", "UNARCHIVE", name="approvalaction"
)
_ACTION_ENUM: Final = sa.Enum("ARCHIVE", "DELETE", "UNARCHIVE", name="approvalaction")
_CHOICE_ENUM: Final = sa.Enum("YES", "ABSTAIN", "NO", name="votechoice")
_VOTE_MODE_ENUM: Final = sa.Enum("EMAIL", "TRUSTED", name="votemode")


def downgrade() -> None:
    op.drop_index("ix_voteinterpretation_release_vote_seq", table_name="voteinterpretation")
    op.rename_table("voteinterpretation", "_voteinterpretation_new")
    op.create_table(
        "voteinterpretation",
        sa.Column("release_key", sa.String(), nullable=False),
        sa.Column("asf_eid", sa.String(), nullable=False),
        sa.Column("choice", _CHOICE_ENUM, nullable=False),
        sa.Column("asf_uid", sa.String(), nullable=False),
        sa.Column("created", sql.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["release_key"],
            ["release.key"],
            name=op.f("fk_voteinterpretation_release_key_release"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("release_key", "asf_eid", name=op.f("pk_voteinterpretation")),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO voteinterpretation (release_key, asf_eid, choice, asf_uid, created)
            SELECT v.release_key, v.asf_eid, v.choice, v.confirmed_by, v.created
            FROM _voteinterpretation_new AS v
            WHERE v.vote_seq = (
                SELECT MAX(v2.vote_seq)
                FROM _voteinterpretation_new AS v2
                WHERE v2.release_key = v.release_key AND v2.asf_eid = v.asf_eid
            )
            """
        )
    )
    op.drop_table("_voteinterpretation_new")

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("vote_mode", _VOTE_MODE_ENUM, nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE release
            SET vote_mode = 'TRUSTED'
            WHERE expedited = 1
            OR EXISTS (SELECT 1 FROM ballotpaper AS b WHERE b.release_key = release.key)
            """
        )
    )
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.create_check_constraint(
            batch_op.f("ck_release_expedited_vote_mode_trusted"),
            "NOT expedited OR vote_mode IS NULL OR vote_mode = 'TRUSTED'",
        )

    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(sa.Column("vote_mode", _VOTE_MODE_ENUM, nullable=True))

    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.alter_column("project_key", existing_type=sa.VARCHAR(), nullable=True)
        batch_op.alter_column("action", existing_type=_ACTION_ENUM, type_=_ACTION_ENUM_PRIOR, existing_nullable=False)
        batch_op.alter_column("cap_question_id", existing_type=sa.INTEGER(), nullable=True)


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM task WHERE UPPER(task_type) = 'VOTE_AUTO_RESOLVE'"))
    op.execute(
        sa.text(
            """
            UPDATE task
            SET task_args = json_remove(task_args, '$.automatic_resolve_when_finished')
            WHERE UPPER(task_type) = 'VOTE_INITIATE'
            AND json_valid(task_args)
            AND json_type(task_args, '$.automatic_resolve_when_finished') IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM task
            WHERE UPPER(task_type) = 'CAP_APPROVAL_RESOLVE'
            AND json_valid(task_args)
            AND json_extract(task_args, '$.approval_request_id') IN (
                SELECT id FROM approvalrequest
                WHERE action IN ('EMAIL_VOTING', 'TRUSTED_VOTING')
                OR project_key IS NULL
                OR cap_question_id IS NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM approvalrequest
            WHERE action IN ('EMAIL_VOTING', 'TRUSTED_VOTING')
            OR project_key IS NULL
            OR cap_question_id IS NULL
            """
        )
    )

    with op.batch_alter_table("approvalrequest", schema=None) as batch_op:
        batch_op.alter_column("action", existing_type=_ACTION_ENUM_PRIOR, type_=_ACTION_ENUM, existing_nullable=False)
        batch_op.alter_column("cap_question_id", existing_type=sa.INTEGER(), nullable=False)
        batch_op.alter_column("project_key", existing_type=sa.VARCHAR(), nullable=False)

    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.drop_column("vote_mode")

    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_release_expedited_vote_mode_trusted"), type_="check")
        batch_op.drop_column("vote_mode")

    # Legacy voteinterpretation rows stored only the confirmer UID without verified voter identity,
    # archive timestamp, or vote_seq, so they cannot be safely converted into official ballots offline.
    op.drop_table("voteinterpretation")
    op.create_table(
        "voteinterpretation",
        sa.Column("release_key", sa.String(), nullable=False),
        sa.Column("vote_seq", sa.Integer(), nullable=False),
        sa.Column("asf_eid", sa.String(), nullable=False),
        sa.Column("vote_round", sa.Integer(), nullable=True),
        sa.Column("voter_asf_uid", sa.String(), nullable=True),
        sa.Column("voter_email", sa.String(), nullable=False),
        sa.Column("voter_fullname", sa.String(), nullable=False),
        sa.Column("choice", _CHOICE_ENUM, nullable=False),
        sa.Column("is_binding_at_confirm", sa.Boolean(), nullable=False),
        sa.Column("email_timestamp", sql.UTCDateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", sa.String(), nullable=False),
        sa.Column("created", sql.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["release_key"],
            ["release.key"],
            name=op.f("fk_voteinterpretation_release_key_release"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("release_key", "vote_seq", "asf_eid", name=op.f("pk_voteinterpretation")),
    )
    op.create_index(
        "ix_voteinterpretation_release_vote_seq",
        "voteinterpretation",
        ["release_key", "vote_seq"],
        unique=False,
    )
