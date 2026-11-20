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

import htpy

import atr.blueprints.get as get
import atr.db as db
import atr.db.interaction as interaction
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
    session: web.Committer,
    _distribution: Literal["distribution/list"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /distribution/list/<project_key>/<version_key>

    audit_guidance This data is public available to any committer so does not check project access
    """
    release = await shared.distribution.release_validated(project_key, version_key, staging=None)
    if release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
        return await _stage_list_page(project_key, version_key)
    if await interaction.release_distribution_checks_held(release):
        heading = ["Third-party distribution list for ", htm.em[f"{project_key!s}-{version_key!s}"]]
        return await _held_page(project_key, version_key, heading, staging=False)
    distributions, tasks = await _get_page_data(project_key, version_key)
    rows = sorted(
        shared.distribution.release_rows(distributions),
        key=lambda dist: (dist.platform.value.name, dist.owner_namespace, dist.package, dist.version),
    )

    block = htm.Block()
    render.html_nav_phase(block, str(project_key), str(version_key), staging=False)
    block.h1["Third-party distribution list for ", htm.em[f"{project_key!s}-{version_key!s}"]]
    if release.is_embargoed:
        _render_embargo_banner(block)
    if len(tasks) > 0:
        block.append(
            shared.distribution.render_distribution_tasks(
                tasks, util.as_url(list_get, project_key=str(project_key), version_key=str(version_key))
            )
        )
    block.p[shared.distribution.summary(rows)]
    block.div(".alert.alert-warning.mb-4", role="alert")[
        htm.p(".fw-semibold.mb-1")["NOTE:"],
        htm.p(".mb-1")[
            "Tools to distribute to third-party platforms automatically are still being developed, "
            "you must do this manually at present, if required. ATR looks for the packages listed here, "
            "and you can record any others with the button below.",
        ],
    ]
    block.append(_list_actions(project_key, version_key, rows, is_admin=session.is_admin))

    not_found = [dist for dist in rows if dist.is_outstanding]
    ignored = [dist for dist in rows if dist.status == sql.DistributionStatus.IGNORED]
    found = [dist for dist in rows if dist.status == sql.DistributionStatus.FOUND]
    if not_found:
        block.h2["Not found"]
        block.append(_list_not_found(project_key, version_key, not_found))
    if ignored:
        block.append(_list_ignored(project_key, version_key, ignored))
    if found:
        block.append(await _list_found(project_key, version_key, found))

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
async def stage_list(
    _session: web.Committer,
    _distribution: Literal["distribution/stage/list"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /distribution/stage/list/<project_key>/<version_key>

    audit_guidance This data is public available to any committer so does not check project access
    """
    return await _stage_list_page(project_key, version_key)


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
    if staging and release.is_embargoed:
        raise errors.ASFQuartException("Embargoed releases cannot be distributed to staging", errorcode=403)
    title = "Create a staging distribution" if staging else "Create a distribution"
    if await interaction.release_distribution_checks_held(release):
        return await _held_page(project, version, [title], staging=staging)

    block = htm.Block()
    render.html_nav_phase(block, str(project), str(version), staging=staging)

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
        htm.a(
            href=util.as_url(stage_list if staging else list_get, project_key=str(project), version_key=str(version))
        )["view the distribution list"],
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
        ).demand(errors.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404))
        distributions = await data.distribution(release_key=release.key).all()
        phase = "compose" if release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT else "finish"
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
            if (t.task_args.get("phase") == phase)
            and (
                (t.status in [sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE, sql.TaskStatus.FAILED])
                or (t.workflow and (t.workflow.status in ["in-progress", "failed"]))
            )
        ]

    return distributions, tasks


async def _held_page(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, heading: Sequence[htm.Element | str], *, staging: bool
) -> str:
    block = htm.Block()
    render.html_nav_phase(block, str(project_key), str(version_key), staging=staging)
    block.h1[*heading]
    block.div(".p-3.mb-4.bg-danger-subtle.border.border-danger.rounded")[shared.distribution.CHECKS_HELD_MESSAGE]
    title = f"Distributions for {project_key!s} {version_key!s}"
    return await template.blank(title, content=block.collect())


def _list_actions(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, rows: Sequence[sql.Distribution], *, is_admin: bool
) -> htm.Element:
    project, version = str(project_key), str(version_key)
    actions: list[htm.Element] = []
    if any(dist.is_outstanding for dist in rows):
        actions.append(
            htm.form(
                ".d-inline-block.m-0.me-2",
                method="post",
                action=util.as_url(post.distribution.check_all, project_key=project, version_key=version),
            )[form.csrf_input(), htpy.button(".btn.btn-primary", type="submit")["Check all now"]]
        )
    actions.append(
        htm.a(".btn.btn-secondary.me-2", href=util.as_url(record, project_key=project, version_key=version))[
            "Record another distribution" if rows else strings.VERIFY_DISTRIBUTION_BUTTON
        ]
    )
    if is_admin:
        actions.append(
            htm.a(".btn.btn-secondary.me-2", href=util.as_url(automate, project_key=project, version_key=version))[
                "Automated distributions"
            ]
        )
    return htm.div(".mb-4")[*actions]


async def _list_found(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, found: Sequence[sql.Distribution]
) -> htm.Element:
    table_rows = []
    for dist in found:
        delete_form = await form.render(
            model_cls=shared.distribution.DeleteForm,
            action=util.as_url(post.distribution.delete, project_key=str(project_key), version_key=str(version_key)),
            form_classes=".d-inline-block.m-0",
            submit_classes="btn-outline-danger btn-sm",
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
        linked = (dist.web_url is not None) and dist.web_url.startswith("https://")
        version = htm.a(href=dist.web_url)[htm.code[dist.version]] if linked else htm.code[dist.version]
        table_rows.append(
            htm.tr[
                htm.td[dist.platform.value.name],
                htm.td[_list_package(dist)],
                htm.td[version],
                htm.td[util.format_datetime(dist.upload_date) if dist.upload_date else "-"],
                htm.td[util.format_datetime(dist.last_checked) if dist.last_checked else "-"],
                htm.td(".text-end")[delete_form],
            ]
        )
    return htm.details(".mb-3.rounded", open=True)[
        htm.summary[htm.span(".badge.bg-secondary.me-2.font-monospace")[str(len(found))], "Found"],
        htm.table(".table.table-sm.table-bordered.table-striped.mt-2")[
            htm.thead[
                htm.tr[
                    htm.th["Platform"],
                    htm.th["Package"],
                    htm.th["Version"],
                    htm.th["Uploaded"],
                    htm.th["Last checked"],
                    htm.th[""],
                ]
            ],
            htm.tbody[*table_rows],
        ],
    ]


def _list_ignored(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, ignored: Sequence[sql.Distribution]
) -> htm.Element:
    action = util.as_url(post.distribution.unignore, project_key=str(project_key), version_key=str(version_key))
    table_rows = [
        htm.tr[
            htm.td[dist.platform.value.name],
            htm.td[_list_package(dist)],
            htm.td[htm.code[dist.version]],
            htm.td[dist.ignored_reason or "-"],
            htm.td[dist.ignored_by or "-"],
            htm.td(".text-end")[
                htm.form(".d-inline-block.m-0", method="post", action=action)[
                    *_list_row_inputs(dist),
                    htpy.button(".btn.btn-sm.btn-outline-secondary", type="submit")["Stop ignoring"],
                ]
            ],
        ]
        for dist in ignored
    ]
    return htm.details(".mb-3.rounded", open=True)[
        htm.summary[htm.span(".badge.bg-secondary.me-2.font-monospace")[str(len(ignored))], "Ignored"],
        htm.table(".table.table-sm.table-bordered.table-striped.mt-2")[
            htm.thead[
                htm.tr[
                    htm.th["Platform"],
                    htm.th["Package"],
                    htm.th["Version"],
                    htm.th["Reason"],
                    htm.th["Ignored by"],
                    htm.th[""],
                ]
            ],
            htm.tbody[*table_rows],
        ],
    ]


def _list_not_found(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, not_found: Sequence[sql.Distribution]
) -> htm.Element:
    project, version = str(project_key), str(version_key)
    version_action = util.as_url(post.distribution.change_version, project_key=project, version_key=version)
    ignore_action = util.as_url(post.distribution.ignore, project_key=project, version_key=version)
    table_rows = []
    for index, dist in enumerate(not_found):
        change = htm.details[
            htm.summary["Change"],
            htm.form(".mt-2", method="post", action=version_action)[
                *_list_row_inputs(dist),
                htpy.label(".form-label", for_=f"distribution-version-{index}")["Version"],
                htm.div(".input-group.input-group-sm.mb-3")[
                    htpy.input(
                        ".form-control.font-monospace",
                        type="text",
                        name="new_version",
                        id=f"distribution-version-{index}",
                        value=dist.version,
                        required=True,
                    ),
                    htpy.button(".btn.btn-outline-primary", type="submit")["Save and check"],
                ],
            ],
            htm.form(method="post", action=ignore_action)[
                *_list_row_inputs(dist),
                htpy.label(".form-label", for_=f"distribution-reason-{index}")[
                    "Reason",
                    htm.span(".text-danger.ms-1", aria_hidden="true")["*"],
                    htm.span(".visually-hidden")["(required)"],
                ],
                htm.div(".input-group.input-group-sm")[
                    htpy.input(
                        ".form-control", type="text", name="reason", id=f"distribution-reason-{index}", required=True
                    ),
                    htpy.button(".btn.btn-outline-secondary", type="submit")["Ignore for this release"],
                ],
            ],
        ]
        package: list[htm.Element | str] = [*_list_package(dist)]
        if dist.is_staged and dist.upload_date:
            package.append(htm.div(".small.text-muted")[f"Staged {util.format_datetime(dist.upload_date)}"])
        table_rows.append(
            htm.tr[
                htm.td[dist.platform.value.name],
                htm.td[package],
                htm.td[htm.code[dist.version]],
                htm.td[util.format_datetime(dist.last_checked) if dist.last_checked else "Not yet checked"],
                htm.td[change],
            ]
        )
    return htm.table(".table.table-sm.table-bordered.table-striped")[
        htm.thead[htm.tr[htm.th["Platform"], htm.th["Package"], htm.th["Version"], htm.th["Last checked"], htm.th[""]]],
        htm.tbody[*table_rows],
    ]


def _list_package(dist: sql.Distribution) -> list[htm.Element | str]:
    if not dist.owner_namespace:
        return [htm.code[dist.package]]
    return [htm.code[dist.owner_namespace], " ", htm.code[dist.package]]


def _list_row_inputs(dist: sql.Distribution) -> list[htm.Element | htm.VoidElement]:
    return [
        form.csrf_input(),
        htpy.input(
            type="hidden", name="platform", value=shared.distribution.DistributionPlatform.from_sql(dist.platform).value
        ),
        htpy.input(type="hidden", name="owner_namespace", value=dist.owner_namespace),
        htpy.input(type="hidden", name="package", value=dist.package),
        htpy.input(type="hidden", name="version", value=dist.version),
    ]


async def _record_form_page(project: safe.ProjectKey, version: safe.VersionKey, staging: bool) -> str:
    """Helper to render the distribution recording form page."""
    release = await shared.distribution.release_validated(project, version, staging=staging)
    title = "Record a staging third-party distribution" if staging else "Record a third-party distribution"
    if await interaction.release_distribution_checks_held(release):
        return await _held_page(project, version, [title], staging=staging)

    block = htm.Block()
    render.html_nav_phase(block, str(project), str(version), staging=staging)

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
        htm.a(
            href=util.as_url(stage_list if staging else list_get, project_key=str(project), version_key=str(version))
        )["list of recorded distributions"],
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
    block.append(shared.distribution.html_platforms_without_namespace())
    block.append(form_html)

    return await template.blank(title, content=block.collect(), javascripts=["distribution-namespace-toggle"])


def _render_embargo_banner(block: htm.Block) -> None:
    block.div(".p-3.mb-4.bg-danger-subtle.border.border-danger.rounded")[
        "This is an expedited security release, and is embargoed. Distributing on third party platforms"
        " makes the release files public, which breaks the embargo. Please ensure that you have the"
        " authority to lift the embargo before distributing. This action is not reversible."
    ]


async def _stage_list_page(project_key: safe.ProjectKey, version_key: safe.VersionKey) -> str:
    distributions, tasks = await _get_page_data(project_key, version_key)
    distributions = [dist for dist in distributions if dist.staging]

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
                tasks, util.as_url(stage_list, project_key=str(project_key), version_key=str(version_key))
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
        if dist.status == sql.DistributionStatus.CHECKING:
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
