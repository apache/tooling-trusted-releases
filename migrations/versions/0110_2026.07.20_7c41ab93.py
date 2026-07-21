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

"""Separate signing keys from the certificates which carry them

Revision ID: 0110_2026.07.20_7c41ab93
Revises: 0109_2026.07.15_91de479d
Create Date: 2026-07-20 13:16:27.542718+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

import atr.models.sql as sql

# Revision identifiers, used by Alembic
revision: str = "0110_2026.07.20_7c41ab93"
down_revision: str | None = "0109_2026.07.15_91de479d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("publicsigningkey", "signingcertificate")

    op.create_table(
        "signingkey",
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("certificate_fingerprint", sa.String(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("key_id", sa.String(), nullable=False),
        sa.Column("algorithm", sa.Integer(), nullable=False),
        sa.Column("length", sa.Integer(), nullable=False),
        sa.Column("created", sql.UTCDateTime(timezone=True), nullable=False),
        sa.Column("expires", sql.UTCDateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("can_sign", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["certificate_fingerprint"],
            ["signingcertificate.fingerprint"],
            name=op.f("fk_signingkey_certificate_fingerprint_signingcertificate"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("fingerprint", name=op.f("pk_signingkey")),
        sa.UniqueConstraint("fingerprint", name=op.f("uq_signingkey_fingerprint")),
    )
    with op.batch_alter_table("signingkey", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_signingkey_certificate_fingerprint"), ["certificate_fingerprint"])
        batch_op.create_index(batch_op.f("ix_signingkey_is_primary"), ["is_primary"])
        batch_op.create_index(batch_op.f("ix_signingkey_key_id"), ["key_id"])

    # Every certificate contributes its own primary key, seeded unconditionally from its own columns
    # so that a block we cannot parse still leaves Artifact.key_fingerprint a row to point at when the
    # constraint is rebuilt below. The parse that follows then corrects the flags and adds the subkeys
    op.execute(
        """
        INSERT INTO signingkey (
            fingerprint, certificate_fingerprint, is_primary, key_id,
            algorithm, length, created, expires, revoked, can_sign
        )
        SELECT
            fingerprint, fingerprint, 1, substr(fingerprint, -16),
            algorithm, length, created, expires, 0, 1
        FROM signingcertificate
        """
    )

    # Subkeys and the true revoked/can_sign flags need the armored block parsed, which SQL cannot do.
    # _signing_key_rows is the same parser the writer runs on every import, so a later sync reproduces
    # exactly this - deterministic enough to derive here despite the migration reaching into app code
    import atr.storage.writers.keys as keys

    connection = op.get_bind()
    signingkey = sa.table(
        "signingkey",
        sa.column("fingerprint"),
        sa.column("certificate_fingerprint"),
        sa.column("is_primary"),
        sa.column("key_id"),
        sa.column("algorithm"),
        sa.column("length"),
        sa.column("created", sql.UTCDateTime(timezone=True)),
        sa.column("expires", sql.UTCDateTime(timezone=True)),
        sa.column("revoked"),
        sa.column("can_sign"),
    )
    derived: list[dict] = []
    for fingerprint, block in connection.execute(
        sa.text("SELECT fingerprint, ascii_armored_key FROM signingcertificate")
    ):
        try:
            rows = keys._signing_key_rows(fingerprint, block)
        except Exception:
            rows = None
        if rows:
            derived.extend(rows)
    if derived:
        statement = sqlite_insert(signingkey)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["fingerprint"],
                set_={column.name: statement.excluded[column.name] for column in signingkey.columns},
            ),
            derived,
        )

    with op.batch_alter_table("signingcertificate", schema=None) as batch_op:
        # Algorithm, length, creation and expiry are per-key facts, now carried by the SigningKey rows
        batch_op.drop_column("algorithm")
        batch_op.drop_column("length")
        batch_op.drop_column("created")
        batch_op.drop_column("expires")
        # rename_table leaves the constraints under their publicsigningkey names, so bring the unique
        # one into line with the naming convention the models expect
        batch_op.drop_constraint("uq_publicsigningkey_fingerprint", type_="unique")
        batch_op.create_unique_constraint(batch_op.f("uq_signingcertificate_fingerprint"), ["fingerprint"])

    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.drop_constraint("fk_artifact_key_fingerprint_publicsigningkey", type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_artifact_key_fingerprint_signingkey"),
            "signingkey",
            ["key_fingerprint"],
            ["fingerprint"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("keylink", schema=None) as batch_op:
        batch_op.drop_constraint("fk_keylink_key_fingerprint_publicsigningkey", type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_keylink_key_fingerprint_signingcertificate"),
            "signingcertificate",
            ["key_fingerprint"],
            ["fingerprint"],
        )


def downgrade() -> None:
    with op.batch_alter_table("keylink", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("fk_keylink_key_fingerprint_signingcertificate"), type_="foreignkey")

    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("fk_artifact_key_fingerprint_signingkey"), type_="foreignkey")

    with op.batch_alter_table("signingcertificate", schema=None) as batch_op:
        batch_op.add_column(sa.Column("algorithm", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("length", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("created", sql.UTCDateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("expires", sql.UTCDateTime(timezone=True), nullable=True))
        batch_op.drop_constraint(op.f("uq_signingcertificate_fingerprint"), type_="unique")
        batch_op.create_unique_constraint("uq_publicsigningkey_fingerprint", ["fingerprint"])

    # An artifact signed by a subkey has no row to point at once this table is gone, so send it back
    # to the certificate which carries that subkey
    op.execute(
        """
        UPDATE artifact
        SET key_fingerprint = (
            SELECT certificate_fingerprint FROM signingkey
            WHERE signingkey.fingerprint = artifact.key_fingerprint
        )
        WHERE key_fingerprint IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE signingcertificate
        SET (algorithm, length, created, expires) = (
            SELECT algorithm, length, created, expires FROM signingkey
            WHERE signingkey.certificate_fingerprint = signingcertificate.fingerprint
              AND signingkey.is_primary = 1
        )
        """
    )

    op.drop_table("signingkey")
    op.rename_table("signingcertificate", "publicsigningkey")

    with op.batch_alter_table("artifact", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_artifact_key_fingerprint_publicsigningkey",
            "publicsigningkey",
            ["key_fingerprint"],
            ["fingerprint"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("keylink", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_keylink_key_fingerprint_publicsigningkey",
            "publicsigningkey",
            ["key_fingerprint"],
            ["fingerprint"],
        )
