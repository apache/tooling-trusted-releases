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

import pathlib
from typing import Final, Literal

import aiofiles
import asfquart.session
import quart.wrappers.response as quart_response
import sqlalchemy.orm as orm
import sqlmodel

import atr.blueprints.get as get
import atr.config as config
import atr.db as db
import atr.htm as htm
import atr.models.sql as sql
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web

_POLICIES: Final = htm.div[
    htm.h1["Release policy"],
    htm.p[
        """Note that the ATR platform will replace the use
        dist.apache.org svn repository where mentioned in
        any of the following policies."""
    ],
    htm.h2["Standard ASF policies"],
    htm.ul_links(
        ("https://www.apache.org/legal/release-policy.html", "Release policy"),
        ("https://www.apache.org/legal/src-headers.html", "Source headers"),
        ("https://www.apache.org/legal/resolved.html", "Third party license"),
        ("https://www.apache.org/foundation/voting.html", "Voting process"),
        ("https://infra.apache.org/release-publishing.html", "Release process"),
    ),
    htm.h2["Additional incubator policies"],
    htm.ul_links(
        ("https://incubator.apache.org/policy/incubation.html#releases", "Incubator release process"),
        ("https://incubator.apache.org/guides/releasemanagement.html#podling_constraints", "Incubator constraints"),
        ("https://incubator.apache.org/policy/incubation.html#disclaimers", "Incubation disclaimer"),
    ),
]


@get.typed
async def about(session: web.Committer, _about: Literal["about"]) -> str:
    """
    URL: /about
    About page.
    """
    return await template.render("about.html")


@get.typed
async def index(session: web.Public, _root: Literal[""]) -> quart_response.Response | str:
    """
    URL: /
    Show public info or an entry portal for participants.
    """
    session_data = await asfquart.session.read()
    if session_data:
        uid = session_data.uid
        if not uid:
            return await template.render("index-public.html")

        phase_sequence = ["Compose", "Vote", "Finish"]
        phase_index_map = {
            sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT: 0,
            sql.ReleasePhase.RELEASE_CANDIDATE: 1,
            sql.ReleasePhase.RELEASE_PREVIEW: 2,
        }

        async with db.session() as data:
            user_projects = await user.projects(uid)
            user_projects.sort(key=lambda p: p.display_name.lower())

            projects_with_releases = []
            projects_without_releases = []

            active_phases = list(phase_index_map.keys())
            for project in user_projects:
                stmt = (
                    sqlmodel.select(sql.Release)
                    .where(
                        sql.Release.project_name == project.name,
                        sql.validate_instrumented_attribute(sql.Release.phase).in_(active_phases),
                    )
                    .options(orm.selectinload(sql.validate_instrumented_attribute(sql.Release.project)))
                    .order_by(sql.validate_instrumented_attribute(sql.Release.created).desc())
                )
                result = await data.execute(stmt)
                active_releases = result.scalars().all()
                completed_releases = (
                    len(await data.release(phase=sql.ReleasePhase.RELEASE, project_name=project.name).all()) > 0
                )

                if active_releases:
                    projects_with_releases.append(
                        {
                            "project": project,
                            "active_releases": active_releases,
                            "completed_releases": completed_releases,
                        }
                    )
                else:
                    projects_without_releases.append(
                        {"project": project, "active_releases": [], "completed_releases": completed_releases}
                    )

        all_projects = projects_with_releases + projects_without_releases

        def sort_key(item: dict) -> str:
            project = item["project"]
            if not isinstance(project, sql.Project):
                return ""
            return project.display_name.lower()

        all_projects.sort(key=sort_key)

        return await template.render(
            "index-committer.html",
            all_projects=all_projects,
            phase_sequence=phase_sequence,
            phase_index_map=phase_index_map,
            format_datetime=util.format_datetime,
        )

    # Public view
    return await template.render("index-public.html")


@get.typed
async def policies(session: web.Public, _policies: Literal["policies"]) -> str:
    """
    URL: /policies
    """
    return await template.blank("Policies", content=_POLICIES)


@get.typed
async def resolved_json(
    session: web.Public, _miscellaneous_resolved_json: Literal["miscellaneous/resolved.json"]
) -> quart_response.Response:
    """
    URL: /miscellaneous/resolved.json
    """
    json_path = pathlib.Path(config.get().PROJECT_ROOT) / "atr" / "static" / "json" / "resolved.json"
    async with aiofiles.open(json_path) as f:
        content = await f.read()
    return quart_response.Response(content, mimetype="application/json")


@get.typed
async def tutorial(session: web.Committer, _tutorial: Literal["tutorial"]) -> str:
    """
    URL: /tutorial
    Tutorial page.
    """
    return await template.render("tutorial.html")
