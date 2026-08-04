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

from typing import TYPE_CHECKING, Final

import semver

import atr.models.calver as calver
import atr.models.sql as sql
import atr.models.validation as validation

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterable

    import atr.db as db

DEFAULT_CYCLE: Final[str] = "default"


def cycle_name_for_version(project: sql.Project, version: str) -> str:
    """Resolve which cycle a version belongs to.

    Returns the cycle name (not the FK key). Projects with no `cycle_match`
    set always return "default". Otherwise the regex is fullmatched against
    the version string and capture-group 1 is returned.

    Raises ValueError if the version doesn't match, the pattern has no
    capture groups, or capture-group 1 captured the empty string.
    """
    if project.cycle_match is None:
        return DEFAULT_CYCLE

    match = validation.compile_project_pattern(project.cycle_match, captures=True).fullmatch(version)
    if match is None:
        raise ValueError(f"Version {version!r} does not match cycle_match for project {project.key!r}")

    if not match.groups():
        raise ValueError(f"cycle_match for project {project.key!r} has no capture groups")

    cycle = match.group(1)
    if not cycle:
        raise ValueError(f"cycle_match for project {project.key!r} captured empty string from version {version!r}")

    return cycle


def display_name(cycle_name: str) -> str:
    """Heading for a cycle. The default one is the catch-all, not a lifecycle."""
    return "No lifecycle information" if cycle_name == DEFAULT_CYCLE else f"Version {cycle_name}"


def headings_needed(cycle_names: Iterable[str]) -> bool:
    """Whether a project's cycles are worth heading at all.

    A project sitting on nothing but the implicit default cycle has no lifecycle
    to show, so its releases stay in one flat list.
    """
    names = list(cycle_names)
    return (len(names) > 1) or any(name != DEFAULT_CYCLE for name in names)


def is_latest_in_cycle(project: sql.Project, release: sql.Release, candidates: Iterable[sql.Release]) -> bool:
    """Whether `release` is the latest full, unarchived release in its cycle.

    Candidates are filtered here, so callers can pass whichever release list they
    already have to hand. A release that can't be placed counts as the latest,
    per latest_release_in_cycle.
    """
    active = [r for r in candidates if (r.phase == sql.ReleasePhase.RELEASE) and (not r.is_archived)]
    latest = latest_release_in_cycle(project, release.version, active)
    return (latest is None) or (latest.key == release.key)


def latest_release_in_cycle(
    project: sql.Project,
    version: str,
    candidates: Iterable[sql.Release],
) -> sql.Release | None:
    """Pick the latest release within the cycle that `version` belongs to.

    Cycle membership and ordering follow prior_release_in_cycle: candidates are
    filtered to the same cycle as `version`, then ranked per the project's
    `version_method`. When the scheme can't rank anything in the cycle, they all
    fall back to the released date together, as SIMPLE projects use throughout.

    Returns None when `version` can't be placed in whichever of those two
    orderings is in play. Callers can't then tell the release apart from the
    latest one, which is the safe way round: ranking it against siblings it
    can't be compared with would make a project's newest release look superseded.
    """
    same_cycle = _same_cycle_candidates(project, version, candidates)
    if not same_cycle:
        return None

    match project.version_method:
        case sql.VersionMethod.SIMPLE:
            return _latest_by_released_date(version, same_cycle)
        case sql.VersionMethod.SEMVER:
            ranked = _ranked_semver(same_cycle)
            target_ranks = _semver_parse(version) is not None
        case sql.VersionMethod.CALVER:
            if not project.calver_format:
                return _latest_by_released_date(version, same_cycle)
            ranked = _ranked_calver(project.calver_format, same_cycle)
            target_ranks = calver.order_key(project.calver_format, version) is not None

    if not ranked:
        # Nothing in the cycle ranks under the scheme, so they all fall back together
        return _latest_by_released_date(version, same_cycle)
    if not target_ranks:
        # The siblings rank but the target doesn't, so it would come back looking
        # superseded by versions it was never compared against
        return None
    return ranked[0][1]


def prior_release_in_cycle(
    project: sql.Project,
    version: str,
    releases: Iterable[sql.Release],
) -> sql.Release | None:
    """Pick the release immediately prior to `version` within its cycle.

    Filters `releases` to those in the same cycle as `version`, re-resolving
    cycle membership through `cycle_name_for_version` so that any change to the
    project's `cycle_match` since the candidates were started is honoured.
    The prior release is then chosen per the project's `version_method`:

      - SIMPLE: the most recently released candidate
      - SEMVER: the highest semver version strictly less than `version`
      - CALVER: the highest calendar version strictly less than `version`,
        ordered through the project's `calver_format` date format
    """
    same_cycle = _same_cycle_candidates(project, version, releases)
    if not same_cycle:
        return None

    match project.version_method:
        case sql.VersionMethod.SIMPLE:
            return _latest_release_by_released(same_cycle)
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
            cycle_name = DEFAULT_CYCLE
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


def _latest_by_released_date(version: str, releases: list[sql.Release]) -> sql.Release | None:
    # An undated target has no place in this ordering, and _latest_release_by_released would
    # drop it and hand back a sibling instead, making it look superseded
    target = next((release for release in releases if release.version == version), None)
    if (target is None) or (target.released is None):
        return None
    return _latest_release_by_released(releases)


def _latest_release_by_released(releases: list[sql.Release]) -> sql.Release | None:
    timed: list[tuple[datetime.datetime, sql.Release]] = [(c.released, c) for c in releases if c.released is not None]
    if not timed:
        return None
    timed.sort(key=lambda pair: pair[0], reverse=True)
    return timed[0][1]


def _prior_release_calver(project: sql.Project, version: str, releases: list[sql.Release]) -> sql.Release | None:
    # Without a format string there's no way to order calendar versions, so fall back to
    # the most recently released candidate, as SIMPLE projects do.
    format_str = project.calver_format
    if not format_str:
        return _latest_release_by_released(releases)
    target = calver.order_key(format_str, version)
    if target is None:
        return None
    for key, candidate in _ranked_calver(format_str, releases):
        if key < target:
            return candidate
    return None


def _prior_release_semver(version: str, releases: list[sql.Release]) -> sql.Release | None:
    target = _semver_parse(version)
    if target is None:
        return None
    for parsed, candidate in _ranked_semver(releases):
        if parsed < target:
            return candidate
    return None


def _ranked_calver(format_str: str, releases: list[sql.Release]) -> list[tuple[tuple[int, int, int, int], sql.Release]]:
    ranked: list[tuple[tuple[int, int, int, int], sql.Release]] = []
    for candidate in releases:
        key = calver.order_key(format_str, candidate.version)
        if key is None:
            continue
        ranked.append((key, candidate))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked


def _ranked_semver(releases: list[sql.Release]) -> list[tuple[semver.VersionInfo, sql.Release]]:
    ranked: list[tuple[semver.VersionInfo, sql.Release]] = []
    for candidate in releases:
        parsed = _semver_parse(candidate.version)
        if parsed is None:
            continue
        ranked.append((parsed, candidate))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked


def _same_cycle_candidates(project: sql.Project, version: str, releases: Iterable[sql.Release]) -> list[sql.Release]:
    try:
        target_cycle = cycle_name_for_version(project, version)
    except ValueError:
        return []

    same_cycle: list[sql.Release] = []
    for candidate in releases:
        try:
            candidate_cycle = cycle_name_for_version(project, candidate.version)
        except ValueError:
            continue
        if candidate_cycle == target_cycle:
            same_cycle.append(candidate)
    return same_cycle


def _semver_parse(version_str: str) -> semver.VersionInfo | None:
    try:
        return semver.VersionInfo.parse(version_str.lstrip("v"))
    except ValueError:
        return None
