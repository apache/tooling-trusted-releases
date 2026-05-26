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

import asfquart.base as base
import htpy

import atr.blueprints.get as get
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get.projects as projects
import atr.get.root as root
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def selected(session: web.Committer, _start: Literal["start"], project_key: safe.ProjectKey) -> str:
    """
    URL: /start/<project_key>
    """
    await session.prevent_confusing_ui_display(project_key)
    async with db.session() as data:
        project = await data.project(
            key=str(project_key), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(base.ASFQuartException(f"Project {project_key} not found", errorcode=404))

    releases = await interaction.all_releases(project)
    content = await _render_page(project, releases)
    javascripts = ["start-cycle-preview"] if project.cycle_match else None
    return await template.blank(
        title=f"Start a new release for {project.display_name}",
        content=content,
        javascripts=javascripts,
    )


def _cycle_preview(project: sql.Project) -> htm.Element | None:
    # Simple projects always use the default cycle, so there's nothing to show.
    if not project.cycle_match:
        return None
    lifecycle_url = util.as_url(projects.view, project_key=str(project.key)) + "?tab=lifecycle"
    config = htpy.div(
        "#start-cycle-config.d-none",
        data_cycle_match=project.cycle_match,
        data_lifecycle_url=lifecycle_url,
    )
    placeholder = htm.div("#start-cycle-preview.alert.alert-light.mt-2")[
        "Enter a version to see which cycle it lands in."
    ]
    return htm.div[config, placeholder]


def _existing_releases(ul: htm.Block, releases: list[sql.Release], max_revisions: int = 18) -> None:
    for i, release in enumerate(releases):
        if i >= max_revisions:
            break

        phase_symbol = _get_phase_symbol(release.phase)
        ul.li(".col-6.col-sm-4.col-md-3.col-lg-2")[
            htm.div(".text-nowrap")[
                htm.span(class_="atr-phase-symbol fs-6")[phase_symbol],
                " ",
                release.version,
            ]
        ]

    if len(releases) > max_revisions:
        ul.li(".col-6.col-sm-4.col-md-3.col-lg-2")[
            htm.div(".text-center")[
                htm.strong["..."],
                " ",
                htm.span(".text-muted.ms-1")[f"{len(releases) - max_revisions} more"],
            ]
        ]


def _get_phase_symbol(phase: sql.ReleasePhase) -> str:
    match phase:
        case sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            return "①"
        case sql.ReleasePhase.RELEASE_CANDIDATE:
            return "②"
        case sql.ReleasePhase.RELEASE_PREVIEW:
            return "③"
        case sql.ReleasePhase.RELEASE:
            return "Ⓡ"


def _pre_submit(project: sql.Project) -> htm.Element | None:
    return _cycle_preview(project)


async def _render_page(project: sql.Project, releases: list[sql.Release]) -> htm.Element:
    page = htm.Block()

    page.h1[f"Start a new release for {project.display_name}"]
    page.p[
        "Starting a new release creates a ",
        htm.strong["release candidate draft"],
        ". You can then add files to this draft before promoting it for voting.",
    ]
    await form.render_block(
        page,
        model_cls=shared.start.StartReleaseForm,
        form_classes=".atr-canary.py-4.px-5.border.rounded",
        submit_classes="btn-primary btn-lg",
        submit_label="Start new release",
        cancel_url=util.as_url(root.index),
        defaults={"project_key": project.key},
        pre_submit=_pre_submit(project),
        skip=["auto_archive_prior"] if not project.policy_auto_archive_prior_release else [],
    )
    if releases:
        page.h2(".mt-5")["Existing releases"]
        with page.block(htm.div, classes=".row") as row:
            with row.block(htm.div, classes=".col-12") as col:
                with col.block(htm.ul, classes=".list-unstyled.row.g-3") as ul:
                    _existing_releases(ul, releases)
    page.h2(".mt-5")["Version numbers, revision serial numbers, and tags"]
    page.p[
        "Your version number should look like ",
        htpy.code["1.2"],
        " or ",
        htpy.code["1.2.3-M1"],
        """ etc. and should not include a release candidate portion, but
        may include an alpha, beta, or milestone portion. Whenever you modify
        your release files before starting a vote, ATR creates a new revision
        serial number like """,
        htpy.code["00002"],
        """ which you can then refer to in the vote announcement email. You
        can also tag revisions, and either the revision serial number or the
        tag can be used in the vote announcement email. The tag can e.g. be
        set to """,
        htpy.code["rc1"],
        ".",
    ]
    return page.collect()
