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

"""Move the dist watcher's layout tunables into the database

Revision ID: 0124_2026.09.04_a3f2c1d4
Revises: 0123_2026.08.27_853c7eed
Create Date: 2026-09-04 10:12:26.514978+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0124_2026.09.04_a3f2c1d4"
down_revision: str | None = "0123_2026.08.27_853c7eed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_ENUM = sa.Enum(
    "project_remap",
    "grouping_bucket",
    "committee_bucket",
    "excluded_part",
    "name_build_suffix",
    "airflow_provider_area",
    name="distrulekind",
)

# The frozensets and dicts that used to live in atr/svn/dist.py, transcribed to seed rows.
# Each carries the reasoning that was an inline comment in the note column. committee null is a
# global rule; pattern holds the matched token for the membership-set kinds; a project remap
# matches committee/subproject and rewrites to target
_SEED: list[dict[str, object]] = [
    # PROJECT_REMAP - dist-path keys the bare-name match can't reach. Per-entry only, a blanket
    # rule mis-hits (camel-karaf is Camel's)
    {
        "kind": "project_remap",
        "committee": "activemq",
        "subproject": "activemq-artemis",
        "target": "artemis",
        "note": "Artemis graduated from ActiveMQ, dist still splits it",
    },
    {
        "kind": "project_remap",
        "committee": "apr",
        "subproject": None,
        "target": "apr-portable-runtime",
        "note": "the committee's top level is the Portable Runtime itself",
    },
    {
        "kind": "project_remap",
        "committee": "httpd",
        "subproject": None,
        "target": "httpd-http-server",
        "note": "the committee's top level is the HTTP Server",
    },
    {"kind": "project_remap", "committee": "sis", "subproject": None, "target": "sis-spatial-information-system"},
    {"kind": "project_remap", "committee": "trafficcontrol", "subproject": None, "target": "traffic-control"},
    {
        "kind": "project_remap",
        "committee": "trafficserver",
        "subproject": None,
        "target": "trafficserver-traffic-server",
    },
    {
        "kind": "project_remap",
        "committee": "xmlgraphics",
        "subproject": "commons",
        "target": "xmlgraphics-xml-graphics-commons",
    },
    # GROUPING_BUCKET - lead dirs that name a distribution bucket, not a subproject
    {"kind": "grouping_bucket", "pattern": "providers"},
    {"kind": "grouping_bucket", "pattern": "source"},
    {"kind": "grouping_bucket", "pattern": "sources"},
    {"kind": "grouping_bucket", "pattern": "binaries"},
    {"kind": "grouping_bucket", "pattern": "bin"},
    {"kind": "grouping_bucket", "pattern": "src"},
    {"kind": "grouping_bucket", "pattern": "releases"},
    # COMMITTEE_BUCKET - buckets scoped to one committee, where the name is a real subproject elsewhere
    {
        "kind": "committee_bucket",
        "committee": "maven",
        "pattern": "plugins",
        "note": "Maven ships its plugins under plugins/, a bucket not a subproject",
    },
    {
        "kind": "committee_bucket",
        "committee": "cordova",
        "pattern": "platforms",
        "note": "cordova ships each platform repo under platforms/, so the name comes from the file",
    },
    {
        "kind": "committee_bucket",
        "committee": "cordova",
        "pattern": "tools",
        "note": "cordova ships its tooling under tools/, so the name comes from the file",
    },
    # EXCLUDED_PART - dirs of bundled third-party packages, never a release
    {"kind": "excluded_part", "pattern": "repos", "note": "bundled third-party packages (bigtop's repos/)"},
    # NAME_BUILD_SUFFIX - build/status tokens dropped from a filename-derived name
    {"kind": "name_build_suffix", "pattern": "src"},
    {"kind": "name_build_suffix", "pattern": "source"},
    {"kind": "name_build_suffix", "pattern": "sources"},
    {"kind": "name_build_suffix", "pattern": "bin"},
    {"kind": "name_build_suffix", "pattern": "binaries"},
    {"kind": "name_build_suffix", "pattern": "incubating"},
    {"kind": "name_build_suffix", "pattern": "v"},
    # AIRFLOW_PROVIDER_AREA - airflow ships its providers as flat calver batches under these dirs
    {"kind": "airflow_provider_area", "committee": "airflow", "pattern": "providers"},
    {"kind": "airflow_provider_area", "committee": "airflow", "pattern": "backport-providers"},
]


def upgrade() -> None:
    op.create_table(
        "distrule",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", _KIND_ENUM, nullable=False),
        sa.Column("committee", sa.String(), nullable=True),
        sa.Column("subproject", sa.String(), nullable=True),
        sa.Column("pattern", sa.String(), nullable=True),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_distrule")),
    )
    op.create_index(op.f("ix_distrule_kind"), "distrule", ["kind"], unique=False)

    seed_table = sa.table(
        "distrule",
        sa.column("kind", sa.String()),
        sa.column("committee", sa.String()),
        sa.column("subproject", sa.String()),
        sa.column("pattern", sa.String()),
        sa.column("target", sa.String()),
        sa.column("enabled", sa.Boolean()),
        sa.column("note", sa.String()),
    )
    rows = [
        {
            "kind": row["kind"],
            "committee": row.get("committee"),
            "subproject": row.get("subproject"),
            "pattern": row.get("pattern"),
            "target": row.get("target"),
            "enabled": True,
            "note": row.get("note"),
        }
        for row in _SEED
    ]
    op.bulk_insert(seed_table, rows)


def downgrade() -> None:
    op.drop_index(op.f("ix_distrule_kind"), table_name="distrule")
    op.drop_table("distrule")
