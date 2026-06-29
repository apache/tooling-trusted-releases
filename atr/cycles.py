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

"""Cycle resolution for releases.

A release belongs to exactly one cycle. For projects with no `cycle_match`
set (today, every project) there's only the "default" cycle. For semver
or calver projects `cycle_match` is a regex applied to the version string
via re.fullmatch; capture-group 1 is the cycle name.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import semver

import atr.calver as calver
import atr.models.sql as sql

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterable

    import atr.db as db

_DEFAULT_CYCLE: Final[str] = "default"


def cycle_name_for_version(project: sql.Project, version: str) -> str:
    """Resolve which cycle a version belongs to.

    Returns the cycle name (not the FK key). Projects with no `cycle_match`
    set always return "default". Otherwise the regex is fullmatched against
    the version string and capture-group 1 is returned.

    Raises ValueError if the version doesn't match, the pattern has no
    capture groups, or capture-group 1 captured the empty string.
    """
    if project.cycle_match is None:
        return _DEFAULT_CYCLE

    match = re.fullmatch(project.cycle_match, version)
    if match is None:
        raise ValueError(f"Version {version!r} does not match cycle_match for project {project.key!r}")

    if not match.groups():
        raise ValueError(f"cycle_match for project {project.key!r} has no capture groups")

    cycle = match.group(1)
    if not cycle:
        raise ValueError(f"cycle_match for project {project.key!r} captured empty string from version {version!r}")

    return cycle


def prior_release_in_cycle(
    project: sql.Project,
    version: str,
    candidates: Iterable[sql.Release],
) -> sql.Release | None:
    """Pick the release immediately prior to `version` within its cycle.

    Filters `candidates` to those in the same cycle as `version`, re-resolving
    cycle membership through `cycle_name_for_version` so that any change to the
    project's `cycle_match` since the candidates were started is honoured.
    The prior release is then chosen per the project's `version_method`:

      - SIMPLE: the most recently released candidate
      - SEMVER: the highest semver version strictly less than `version`
      - CALVER: the highest calendar version strictly less than `version`,
        ordered through the project's `calver_format` date format
    """
    try:
        target_cycle = cycle_name_for_version(project, version)
    except ValueError:
        return None

    same_cycle: list[sql.Release] = []
    for candidate in candidates:
        try:
            candidate_cycle = cycle_name_for_version(project, candidate.version)
        except ValueError:
            continue
        if candidate_cycle == target_cycle:
            same_cycle.append(candidate)

    if not same_cycle:
        return None

    match project.version_method:
        case sql.VersionMethod.SIMPLE:
            return _prior_release_by_released(same_cycle)
        case sql.VersionMethod.SEMVER:
            return _prior_release_semver(version, same_cycle)
        case sql.VersionMethod.CALVER:
            return _prior_release_calver(project, version, same_cycle)


async def reassign_release_cycles(data: db.Session, project: sql.Project) -> None:
    """Re-resolve cycle membership for every release in the project.

    Releases whose version doesn't match the current `cycle_match` fall
    back to the default cycle. New cycles get auto-created in the same
    transaction.
    """
    releases = await data.release(project_key=str(project.key)).all()
    for release in releases:
        try:
            cycle_name = cycle_name_for_version(project, release.version)
        except ValueError:
            cycle_name = _DEFAULT_CYCLE
        new_cycle_key = f"{project.key}-{cycle_name}"
        if release.cycle_key == new_cycle_key:
            continue
        if not await data.project_cycle(cycle_key=new_cycle_key).get():
            data.add(
                sql.ProjectCycle(
                    cycle_key=new_cycle_key,
                    cycle=cycle_name,
                    project_key=project.key,
                    lts=False,
                )
            )
        release.cycle_key = new_cycle_key


def _prior_release_by_released(candidates: list[sql.Release]) -> sql.Release | None:
    timed: list[tuple[datetime.datetime, sql.Release]] = [(c.released, c) for c in candidates if c.released is not None]
    if not timed:
        return None
    timed.sort(key=lambda pair: pair[0], reverse=True)
    return timed[0][1]


def _prior_release_calver(project: sql.Project, version: str, candidates: list[sql.Release]) -> sql.Release | None:
    # Without a format string there's no way to order calendar versions, so fall back to
    # the most recently released candidate, as SIMPLE projects do.
    format_str = project.calver_format
    if not format_str:
        return _prior_release_by_released(candidates)
    target = calver.order_key(format_str, version)
    if target is None:
        return None
    ranked: list[tuple[tuple[int, int, int, int], sql.Release]] = []
    for candidate in candidates:
        key = calver.order_key(format_str, candidate.version)
        if key is None:
            continue
        if key >= target:
            continue
        ranked.append((key, candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1]


def _prior_release_semver(version: str, candidates: list[sql.Release]) -> sql.Release | None:
    target = _semver_parse(version)
    if target is None:
        return None
    ranked: list[tuple[semver.VersionInfo, sql.Release]] = []
    for candidate in candidates:
        parsed = _semver_parse(candidate.version)
        if parsed is None:
            continue
        if parsed >= target:
            continue
        ranked.append((parsed, candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1]


def _semver_parse(version_str: str) -> semver.VersionInfo | None:
    try:
        return semver.VersionInfo.parse(version_str.lstrip("v"))
    except ValueError:
        return None
