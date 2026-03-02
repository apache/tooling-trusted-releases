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
from typing import Literal

import cmarkgfm
import markupsafe
import quart

import atr.blueprints.get as get
import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.get.vote as vote
import atr.htm as htm
import atr.models.safe as safe
import atr.render as render
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def selected(
    _session: web.Public,
    _checklist: Literal["checklist"],
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
) -> str:
    async with db.session() as data:
        release = await data.release(
            project_name=str(project_name),
            version=str(version_name),
            _project=True,
            _committee=True,
            _project_release_policy=True,
        ).get()

        if release is None:
            quart.abort(404)

        project = release.project
        checklist_markdown = project.policy_release_checklist

        if not checklist_markdown:
            quart.abort(404)

        committee = release.committee
        if committee is None:
            quart.abort(404)

        latest_revision = await interaction.latest_revision(release, caller_data=data)

    substituted_markdown = construct.checklist_body(
        checklist_markdown,
        project=project,
        version_name=version_name,
        committee=committee,
        revision=latest_revision,
    )
    checklist_html = markupsafe.Markup(cmarkgfm.github_flavored_markdown_to_html(substituted_markdown))

    page = htm.Block()
    render.html_nav(
        page,
        back_url=util.as_url(vote.selected, project_name=str(project_name), version_name=str(version_name)),
        back_anchor=f"Vote on {project.short_display_name} {version_name!s}",
        phase="VOTE",
    )
    page.h1["Release checklist"]
    page.p(".text-secondary")[
        "Checklist for ",
        htm.strong[project.short_display_name],
        " version ",
        str(version_name),
        ":",
    ]
    page.div(".checklist-content.mt-4")[checklist_html]

    return await template.blank(
        title=f"Release checklist for {project.short_display_name} {version_name!s}",
        content=page.collect(),
    )
