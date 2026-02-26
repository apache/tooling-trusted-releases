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

import datetime
from typing import Literal

import asfquart.base as base

import atr.blueprints.get as get
import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def finished(
    _session: web.Public, _releases_finished: Literal["releases/finished"], project_name: safe.ProjectName
) -> str:
    """
    URL: /releases/finished/<project_name>
    View all finished releases for a project.
    """
    async with db.session() as data:
        project = await data.project(name=str(project_name), status=sql.ProjectStatus.ACTIVE).demand(
            base.ASFQuartException(f"Project {project_name} not found", errorcode=404)
        )

        releases = await data.release(
            project_name=project.name,
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

    projects = {}
    for release in releases:
        if release.project.display_name not in projects:
            projects[release.project.display_name] = (release.project, 1)
        else:
            projects[release.project.display_name] = (release.project, projects[release.project.display_name][1] + 1)

    return await template.render(
        "releases.html",
        projects=projects,
        releases=releases,
    )


@get.typed
async def select(
    _session: web.Committer, _release_select: Literal["release/select"], project_name: safe.ProjectName
) -> str:
    """
    URL: /release/select/<project_name>
    Show releases in progress for a project.
    """
    async with db.session() as data:
        project = await data.project(name=str(project_name), status=sql.ProjectStatus.ACTIVE, _releases=True).demand(
            base.ASFQuartException(f"Project {project_name} not found", errorcode=404)
        )
        releases = await interaction.releases_in_progress(project)
    return await template.render(
        "release-select.html", project=project, releases=releases, format_datetime=util.format_datetime
    )
