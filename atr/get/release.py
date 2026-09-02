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
from typing import Literal

import asfquart.base as base

import atr.blueprints.get as get
import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web


@get.typed
async def finished(
    _session: web.Public,
    _releases_finished: Literal["releases/finished"],
    project_key: safe.ProjectKey,
    query_args: web.PageQuery,
) -> str:
    """
    URL: /releases/finished/<project_key>
    View all finished releases for a project.
    """
    try:
        validation.pagination_args_validate(query_args)
    except ValueError as e:
        raise base.ASFQuartException(str(e), errorcode=400)
    async with db.session() as data:
        project = await data.project(key=str(project_key), status=sql.ProjectStatus.ACTIVE).demand(
            base.ASFQuartException(f"Project {project_key} not found", errorcode=404)
        )

        releases = await data.release(
            project_key=project.key,
            phase=sql.ReleasePhase.RELEASE,
            _committee=True,
        ).all()

    releases = sorted(releases, key=_release_sort_key, reverse=True)
    count = len(releases)
    page_releases = releases[query_args.offset : (query_args.offset + query_args.limit)]
    page = web.page_nav(query_args.offset, query_args.limit, count, len(page_releases))

    return await template.render(
        "releases-finished.html",
        project=project,
        releases=page_releases,
        count=count,
        limit=query_args.limit,
        page=page,
    )


@get.typed
async def releases(_session: web.Committer, _releases: Literal["releases"]) -> str:
    """
    URL: /releases
    View all releases.
    """
    async with db.session() as data:
        latest = await interaction.project_latest_finished(data)
        projects = await data.project(_committee=True).all()

    return await template.render(
        "releases.html",
        catalog=_committee_release_catalog(projects, latest),
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


def _committee_release_catalog(
    projects: Sequence[sql.Project], latest: dict[str, tuple[int, str, datetime.datetime | None]]
) -> list[_CommitteeReleaseEntry]:
    committees: dict[str, sql.Committee] = {}
    by_committee: dict[str, list[_ProjectReleaseEntry]] = collections.defaultdict(list)
    for project in projects:
        finished = latest.get(project.key)
        if finished is None:
            continue
        committee = project.committee
        if committee is None:
            # Nothing to group it under here, so skip it. Shouldn't happen in practice.
            continue
        finished_count, latest_version, latest_date = finished
        committees[committee.key] = committee
        by_committee[committee.key].append(
            _ProjectReleaseEntry(
                project=project,
                finished_count=finished_count,
                latest_version=latest_version or None,
                latest_date=latest_date,
            )
        )

    for entries in by_committee.values():
        entries.sort(key=lambda entry: interaction.project_order_key(entry.project, entry.latest_date))
    catalog = [
        _CommitteeReleaseEntry(committee=committees[key], projects=entries) for key, entries in by_committee.items()
    ]
    catalog.sort(key=lambda entry: entry.committee.display_name.lower())
    return catalog


def _release_sort_key(release: sql.Release) -> tuple[datetime.datetime, str]:
    # Same key finished() uses: prefer the released date, fall back to created
    return (release.released or release.created, release.key)
