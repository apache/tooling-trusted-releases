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

"""
Load the dist watcher's tunables from the dist_rule table into a DistRules snapshot.

Kept out of svn/dist.py so the decomposer stays a pure module with no database imports.
The app loads through load(); backfill scripts that read the database over raw sqlite build
the same snapshot from snapshot(), so the row-to-rules mapping lives in one place.
"""

from collections.abc import Iterable
from typing import NamedTuple

import atr.db as db
import atr.models.sql as sql
import atr.svn.dist as dist


class Row(NamedTuple):
    # One dist_rule row reduced to the columns the snapshot reads, so an ORM row and a raw
    # sqlite tuple reach snapshot() the same way. kind matches a DistRuleKind by value
    kind: str
    committee: str | None
    subproject: str | None
    pattern: str | None
    target: str | None


def empty() -> dist.DistRules:
    # A snapshot with no rules loaded. The decomposer still finds versions structurally; it just
    # knows no buckets or remaps. Useful as a non-optional default a caller overwrites once it has
    # a database to load from
    return dist.DistRules(
        project_remaps={},
        grouping_buckets=frozenset(),
        committee_buckets=frozenset(),
        excluded_parts=frozenset(),
        name_build_suffixes=frozenset(),
        airflow_provider_areas=frozenset(),
    )


async def load(data: db.Session) -> dist.DistRules:
    # Only enabled rows join the snapshot; a disabled row stays in the table with its note
    rows = await data.dist_rule(enabled=True).all()
    return snapshot(Row(str(row.kind), row.committee, row.subproject, row.pattern, row.target) for row in rows)


def snapshot(rows: Iterable[Row]) -> dist.DistRules:
    # Group the rows by kind once, then read each collection off its kind's rows. A row missing the
    # column its kind needs is skipped in the helper rather than trusted, so a half-filled row can't
    # corrupt the snapshot
    by_kind: dict[str, list[Row]] = {}
    for row in rows:
        by_kind.setdefault(str(row.kind), []).append(row)

    def of(kind: sql.DistRuleKind) -> list[Row]:
        return by_kind.get(str(kind), [])

    return dist.DistRules(
        project_remaps=_remaps(of(sql.DistRuleKind.PROJECT_REMAP)),
        grouping_buckets=_patterns(of(sql.DistRuleKind.GROUPING_BUCKET)),
        committee_buckets=_committee_patterns(of(sql.DistRuleKind.COMMITTEE_BUCKET)),
        excluded_parts=_patterns(of(sql.DistRuleKind.EXCLUDED_PART)),
        name_build_suffixes=_patterns(of(sql.DistRuleKind.NAME_BUILD_SUFFIX)),
        airflow_provider_areas=_patterns(of(sql.DistRuleKind.AIRFLOW_PROVIDER_AREA)),
    )


def _committee_patterns(rows: list[Row]) -> frozenset[tuple[str, str]]:
    return frozenset(
        (row.committee, row.pattern) for row in rows if (row.committee is not None) and (row.pattern is not None)
    )


def _patterns(rows: list[Row]) -> frozenset[str]:
    return frozenset(row.pattern for row in rows if row.pattern is not None)


def _remaps(rows: list[Row]) -> dict[tuple[str, str | None], str]:
    return {
        (row.committee, row.subproject): row.target
        for row in rows
        if (row.committee is not None) and (row.target is not None)
    }
