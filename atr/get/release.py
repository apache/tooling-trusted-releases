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

import collections
import dataclasses
import datetime
from collections.abc import Sequence
from typing import Final, Literal

import asfquart.base as base

import atr.blueprints.get as get
import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web

# Sorts a project with no releases after every dated one
_UNDATED: Final[float] = float("inf")


@get.typed
async def finished(
    _session: web.Public, _releases_finished: Literal["releases/finished"], project_key: safe.ProjectKey
) -> str:
    """
    URL: /releases/finished/<project_key>
    View all finished releases for a project.
    """
    async with db.session() as data:
        project = await data.project(key=str(project_key), status=sql.ProjectStatus.ACTIVE).demand(
            base.ASFQuartException(f"Project {project_key} not found", errorcode=404)
        )

        releases = await data.release(
            project_key=project.key,
            phase=sql.ReleasePhase.RELEASE,
            _committee=True,
        ).all()

    def sort_releases(release: sql.Release) -> datetime.datetime:
        return release.released or release.created

    releases = sorted(releases, key=sort_releases, reverse=True)

    return await template.render(
        "releases-finished.html", project=project, releases=releases, format_datetime=util.format_datetime
    )


@get.typed
async def releases(_session: web.Public, _releases: Literal["releases"]) -> str:
    """
    URL: /releases
    View all releases.
    """
    # Releases are public, so we don't need to filter by user
    async with db.session() as data:
        releases = await data.release(
            phase=sql.ReleasePhase.RELEASE,
            _committee=True,
            _project=True,
        ).all()

    return await template.render(
        "releases.html",
        catalog=_committee_release_catalog(releases),
    )


@get.typed
async def select(
    session: web.Committer, _release_select: Literal["release/select"], project_key: safe.ProjectKey
) -> str:
    """
    URL: /release/select/<project_key>
    Show releases in progress for a project.
    """
    await session.prevent_confusing_ui_display(project_key)
    async with db.session() as data:
        project = await data.project(key=str(project_key), status=sql.ProjectStatus.ACTIVE).demand(
            base.ASFQuartException(f"Project {project_key} not found", errorcode=404)
        )
        releases = await interaction.releases_in_progress(project)
    if not user.can_view_embargoed_release(project.committee, session.uid, is_member=session.is_member):
        releases = [r for r in releases if (not r.is_embargoed)]
    return await template.render(
        "release-select.html", project=project, releases=releases, format_datetime=util.format_datetime
    )


@dataclasses.dataclass
class _ProjectReleaseEntry:
    project: sql.Project
    finished_count: int
    latest_version: str | None
    latest_date: datetime.datetime | None


@dataclasses.dataclass
class _CommitteeReleaseEntry:
    committee: sql.Committee
    projects: list[_ProjectReleaseEntry]


def _committee_release_catalog(releases: Sequence[sql.Release]) -> list[_CommitteeReleaseEntry]:
    committees: dict[str, sql.Committee] = {}
    by_committee: dict[str, list[sql.Release]] = collections.defaultdict(list)
    for release in releases:
        committee = release.project.committee
        if committee is None:
            # Nothing to group it under here, so skip it. Shouldn't happen in practice.
            continue
        committees[committee.key] = committee
        by_committee[committee.key].append(release)

    catalog = [
        _CommitteeReleaseEntry(committee=committees[key], projects=_project_entries(committee_releases))
        for key, committee_releases in by_committee.items()
    ]
    catalog.sort(key=lambda entry: entry.committee.display_name.lower())
    return catalog


def _project_entries(releases: list[sql.Release]) -> list[_ProjectReleaseEntry]:
    by_project: dict[str, list[sql.Release]] = collections.defaultdict(list)
    for release in releases:
        by_project[release.project.key].append(release)

    entries: list[_ProjectReleaseEntry] = []
    for project_releases in by_project.values():
        latest = max(project_releases, key=_release_sort_key)
        entries.append(
            _ProjectReleaseEntry(
                project=latest.project,
                finished_count=len(project_releases),
                latest_version=latest.version or None,
                latest_date=latest.released or None,
            )
        )
    entries.sort(key=_project_entry_sort_key)
    return entries


def _project_entry_sort_key(entry: _ProjectReleaseEntry) -> tuple[bool, float, str]:
    project = entry.project
    # A project keyed after its committee is the main one; umbrella committees have none
    # TODO: This could use a lack of "super_project_id" but we'd need a rule to encode these from history
    is_main = (project.committee_key is not None) and (str(project.key) == str(project.committee_key))
    recency = -entry.latest_date.timestamp() if entry.latest_date else _UNDATED
    return (not is_main, recency, project.display_name.lower())


def _release_sort_key(release: sql.Release) -> datetime.datetime:
    # Same key finished() uses: prefer the released date, fall back to created
    return release.released or release.created
