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
from collections.abc import Sequence
from typing import Literal

import asfquart.base as base
import htpy

import atr.blueprints.get as get
import atr.db as db
import atr.errors as errors
import atr.form as form
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.strings as strings
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def automate(
    session: web.Committer,
    _distribution: Literal["distribution/automate"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /distribution/automate/<project_key>/<version>
    """
    await session.prevent_confusing_ui_display(project_key)
    return await _automate_form_page(project_key, version_key, staging=False)


@get.typed
async def list_get(
    _session: web.Committer,
    _distribution: Literal["distribution/list"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /distribution/list/<project_key>/<version_key>

    audit_guidance This data is public available to any committer so does not check project access
    """
    distributions, tasks = await _get_page_data(project_key, version_key)

    block = htm.Block()

    release = await shared.distribution.release_validated(project_key, version_key, staging=None)
    staging = release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    render.html_nav_phase(block, str(project_key), str(version_key), staging)

    record_a_distribution = htm.a(
        ".btn.btn-primary",
        href=util.as_url(
            stage_record if staging else record,
            project_key=str(project_key),
            version_key=str(version_key),
        ),
    )[strings.VERIFY_DISTRIBUTION_BUTTON]

    # Distribution list for project-version
    block.h1["Third-party distribution list for ", htm.em[f"{project_key!s}-{version_key!s}"]]

    if len(tasks) > 0:
        block.append(
            shared.distribution.render_distribution_tasks(
                tasks, util.as_url(list_get, project_key=str(project_key), version_key=str(version_key))
            )
        )

    if not distributions:
        block.p["No distributions found."]
        block.p[record_a_distribution]
        return await template.blank(
            "Distribution list",
            content=block.collect(),
        )
    block.p["Here are all of the third-party distributions recorded for this release."]
    block.p[record_a_distribution]
    # Table of contents
    block.append(htm.ul_links(*[(f"#distribution-{dist.identifier}", dist.title) for dist in distributions]))

    ## Distributions on third-party platforms
    block.h2["Distributions on third-party platforms"]
    block.p[
        "These are distributions that are hosted on third-party platforms such as Maven Central, PyPI, or Docker Hub."
    ]
    for dist in distributions:
        title_extra = []
        if dist.pending:
            title_extra.append(htpy.small(".text-muted")[" (this distribution is being verified by ATR)"])
        ### Platform package version
        block.h3(
            # Cannot use "#id" here, because the ID contains "."
            # If an ID contains ".", htm parses that as a class
            id=f"distribution-{dist.identifier}"
        )[dist.title, *title_extra]
        tbody = htm.tbody[
            shared.distribution.html_tr("Release name", str(dist.release_key)),
            shared.distribution.html_tr("Platform", dist.platform.value.name),
            shared.distribution.html_tr("Owner or Namespace", dist.owner_namespace or "-"),
            shared.distribution.html_tr("Package", dist.package),
            shared.distribution.html_tr("Version", dist.version),
            shared.distribution.html_tr("Staging", "Yes" if dist.staging else "No"),
            shared.distribution.html_tr("Upload date", str(dist.upload_date)),
            shared.distribution.html_tr_a("API URL", dist.api_url),
            shared.distribution.html_tr_a("Web URL", dist.web_url),
        ]
        block.table(".table.table-striped.table-bordered")[tbody]

        delete_form = await form.render(
            model_cls=shared.distribution.DeleteForm,
            action=util.as_url(post.distribution.delete, project_key=str(project_key), version_key=str(version_key)),
            form_classes=".d-inline-block.m-0",
            submit_classes="btn-danger btn-sm",
            submit_label="Delete",
            empty=True,
            defaults={
                "release_key": dist.release_key,
                "platform": shared.distribution.DistributionPlatform.from_sql(dist.platform),
                "owner_namespace": dist.owner_namespace or "",
                "package": dist.package,
                "version": dist.version,
            },
            confirm=("Are you sure you want to delete this distribution? This cannot be undone."),
        )
        block.append(htm.div(".mb-3")[delete_form])

    title = f"Distribution list for {project_key!s} {version_key!s}"
    return await template.blank(title, content=block.collect())


@get.typed
async def record(
    session: web.Committer,
    _distribution: Literal["distribution/record"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /distribution/record/<project_key>/<version_key>
    """
    await session.prevent_confusing_ui_display(project_key)
    return await _record_form_page(project_key, version_key, staging=False)


@get.typed
async def stage_automate(
    session: web.Committer,
    _distribution: Literal["distribution/stage/automate"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /distribution/stage/automate/<project_key>/<version_key>
    """
    await session.prevent_confusing_ui_display(project_key)
    return await _automate_form_page(project_key, version_key, staging=True)


@get.typed
async def stage_record(
    session: web.Committer,
    _distribution: Literal["distribution/stage/record"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /distribution/stage/record/<project_key>/<version_key>
    """
    await session.prevent_confusing_ui_display(project_key)
    return await _record_form_page(project_key, version_key, staging=True)


async def _automate_form_page(project: safe.ProjectKey, version: safe.VersionKey, staging: bool) -> str:
    """Helper to render the distribution automation form page."""
    release = await shared.distribution.release_validated(project, version, staging=staging)

    block = htm.Block()
    render.html_nav_phase(block, str(project), str(version), staging=staging)

    title = "Create a staging distribution" if staging else "Create a distribution"
    block.h1[title]

    if (not staging) and release.is_embargoed:
        _render_embargo_banner(block)

    block.p[
        "Create a distribution of ",
        htm.strong[f"{project}-{version}"],
        " using the form below.",
    ]
    block.p[
        "You can also ",
        htm.a(href=util.as_url(list_get, project_key=str(project), version_key=str(version)))[
            "view the distribution list"
        ],
        ".",
    ]

    # Determine the action based on staging
    action = (
        util.as_url(post.distribution.stage_automate_selected, project_key=str(project), version_key=str(version))
        if staging
        else util.as_url(post.distribution.automate_selected, project_key=str(project), version_key=str(version))
    )

    # Render the distribution form
    form_html = await form.render(
        model_cls=shared.distribution.DistributionAutomateForm,
        submit_label="Distribute",
        action=action,
        defaults={"package": str(project), "version": str(version)},
    )
    block.append(form_html)

    return await template.blank(title, content=block.collect())


async def _get_page_data(
    project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> tuple[Sequence[sql.Distribution], Sequence[sql.Task]]:
    """Get all the data needed to render the finish page."""
    async with db.session() as data:
        via = sql.validate_instrumented_attribute
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
        ).demand(base.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404))
        distributions = await data.distribution(release_key=release.key).all()
        tasks = [
            t
            for t in (
                await data.task(
                    project_key=str(project_key),
                    version_key=str(version_key),
                    revision_number=release.latest_revision_number,
                    task_type=sql.TaskType.DISTRIBUTION_WORKFLOW,
                    _workflow=True,
                )
                .order_by(sql.sqlmodel.desc(via(sql.Task.started)))
                .all()
            )
            if (t.status in [sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE, sql.TaskStatus.FAILED])
            or (t.workflow and (t.workflow.status in ["in-progress", "failed"]))
        ]

    return distributions, tasks


async def _record_form_page(project: safe.ProjectKey, version: safe.VersionKey, staging: bool) -> str:
    """Helper to render the distribution recording form page."""
    release = await shared.distribution.release_validated(project, version, staging=staging)

    block = htm.Block()
    render.html_nav_phase(block, str(project), str(version), staging=staging)

    title = "Record a staging third-party distribution" if staging else "Record a third-party distribution"
    block.h1[title]

    if (not staging) and release.is_embargoed:
        _render_embargo_banner(block)

    block.p[
        "This form allows you to record a distribution of ",
        htm.strong[f"{project}-{version}"],
        " to third-party distribution channels. ATR will verify that this distribution exists before ",
        "recording it.",
    ]
    block.p[
        "You can also view the ",
        htm.a(href=util.as_url(list_get, project_key=str(project), version_key=str(version)))[
            "list of recorded distributions"
        ],
        ".",
    ]

    # Determine the action based on staging
    action = (
        util.as_url(post.distribution.stage_record_selected, project_key=str(project), version_key=str(version))
        if staging
        else util.as_url(post.distribution.record_selected, project_key=str(project), version_key=str(version))
    )

    # Staging only works for some platforms, so don't offer the others on a stage form (#1263)
    enum_filter_include = None
    if staging:
        stageable = [
            shared.distribution.DistributionPlatform.from_sql(platform).value
            for platform in shared.distribution.STAGEABLE_PLATFORMS
        ]
        enum_filter_include = {"platform": stageable}

    # Render the distribution form
    form_html = await form.render(
        model_cls=shared.distribution.DistributionRecordForm,
        submit_label="Confirm distribution",
        action=action,
        defaults={"package": str(project), "version": str(version)},
        enum_filter_include=enum_filter_include,
    )
    block.append(form_html)

    return await template.blank(title, content=block.collect())


def _render_embargo_banner(block: htm.Block) -> None:
    block.div(".p-3.mb-4.bg-danger-subtle.border.border-danger.rounded")[
        "This is an expedited security release, and is embargoed. Distributing on third party platforms"
        " makes the release files public, which breaks the embargo. Please ensure that you have the"
        " authority to lift the embargo before distributing. This action is not reversible."
    ]
