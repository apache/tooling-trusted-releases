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


import atr.blueprints.get as get
import atr.db as db
import atr.form as form
import atr.htm as htm
import atr.models.sql as sql
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.template as template
import atr.util as util
import atr.web as web


@get.committer("/distribution/automate/<project>/<version>")
async def automate(session: web.Committer, project: str, version: str) -> str:
    return await _automate_form_page(project, version, staging=False)


@get.committer("/distributions/list/<project_name>/<version_name>")
async def list_get(session: web.Committer, project_name: str, version_name: str) -> str:
    async with db.session() as data:
        distributions = await data.distribution(
            release_name=sql.release_name(project_name, version_name),
        ).all()

    block = htm.Block()

    release = await shared.distribution.release_validated(project_name, version_name, staging=None)
    staging = release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    render.html_nav_phase(block, project_name, version_name, staging)

    record_a_distribution = htm.a(
        ".btn.btn-primary",
        href=util.as_url(
            stage_record if staging else record,
            project=project_name,
            version=version_name,
        ),
    )["Record a distribution"]

    # Distribution list for project-version
    block.h1["Distribution list for ", htm.em[f"{project_name}-{version_name}"]]
    if not distributions:
        block.p["No distributions found."]
        block.p[record_a_distribution]
        return await template.blank(
            "Distribution list",
            content=block.collect(),
        )
    block.p["Here are all of the distributions recorded for this release."]
    block.p[record_a_distribution]
    # Table of contents
    block.append(htm.ul_links(*[(f"#distribution-{dist.identifier}", dist.title) for dist in distributions]))

    ## Distributions
    block.h2["Distributions"]
    for dist in distributions:
        ### Platform package version
        block.h3(
            # Cannot use "#id" here, because the ID contains "."
            # If an ID contains ".", htm parses that as a class
            id=f"distribution-{dist.identifier}"
        )[dist.title]
        tbody = htm.tbody[
            shared.distribution.html_tr("Release name", dist.release_name),
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

        delete_form = form.render(
            model_cls=shared.distribution.DeleteForm,
            action=util.as_url(post.distribution.delete, project=project_name, version=version_name),
            form_classes=".d-inline-block.m-0",
            submit_classes="btn-danger btn-sm",
            submit_label="Delete",
            empty=True,
            defaults={
                "release_name": dist.release_name,
                "platform": shared.distribution.DistributionPlatform.from_sql(dist.platform),
                "owner_namespace": dist.owner_namespace or "",
                "package": dist.package,
                "version": dist.version,
            },
            confirm=("Are you sure you want to delete this distribution? This cannot be undone."),
        )
        block.append(htm.div(".mb-3")[delete_form])

    title = f"Distribution list for {project_name} {version_name}"
    return await template.blank(title, content=block.collect())


@get.committer("/distribution/record/<project>/<version>")
async def record(session: web.Committer, project: str, version: str) -> str:
    return await _record_form_page(project, version, staging=False)


@get.committer("/distribution/stage/automate/<project>/<version>")
async def stage_automate(session: web.Committer, project: str, version: str) -> str:
    return await _automate_form_page(project, version, staging=True)


@get.committer("/distribution/stage/record/<project>/<version>")
async def stage_record(session: web.Committer, project: str, version: str) -> str:
    return await _record_form_page(project, version, staging=True)


async def _automate_form_page(project: str, version: str, staging: bool) -> str:
    """Helper to render the distribution automation form page."""
    await shared.distribution.release_validated(project, version, staging=staging)

    block = htm.Block()
    render.html_nav_phase(block, project, version, staging=staging)

    title = "Create a staging distribution" if staging else "Create a distribution"
    block.h1[title]

    block.p[
        "Create a distribution of ",
        htm.strong[f"{project}-{version}"],
        " using the form below.",
    ]
    block.p[
        "You can also ",
        htm.a(href=util.as_url(list_get, project_name=project, version_name=version))["view the distribution list"],
        ".",
    ]

    # Determine the action based on staging
    action = (
        util.as_url(post.distribution.stage_automate_selected, project=project, version=version)
        if staging
        else util.as_url(post.distribution.automate_selected, project=project, version=version)
    )

    # TODO: Reuse the same form for now - maybe we can combine this and the function below adding an automate=True arg
    # Render the distribution form
    form_html = form.render(
        model_cls=shared.distribution.DistributeForm,
        submit_label="Distribute",
        action=action,
        defaults={"package": project, "version": version},
    )
    block.append(form_html)

    return await template.blank(title, content=block.collect())


async def _record_form_page(project: str, version: str, staging: bool) -> str:
    """Helper to render the distribution recording form page."""
    await shared.distribution.release_validated(project, version, staging=staging)

    block = htm.Block()
    render.html_nav_phase(block, project, version, staging=staging)

    title = "Record a manual staging distribution" if staging else "Record a manual distribution"
    block.h1[title]

    block.p[
        "Record a manual distribution of ",
        htm.strong[f"{project}-{version}"],
        " using the form below.",
    ]
    block.p[
        "You can also ",
        htm.a(href=util.as_url(list_get, project_name=project, version_name=version))["view the distribution list"],
        ".",
    ]

    # Determine the action based on staging
    action = (
        util.as_url(post.distribution.stage_record_selected, project=project, version=version)
        if staging
        else util.as_url(post.distribution.record_selected, project=project, version=version)
    )

    # Render the distribution form
    form_html = form.render(
        model_cls=shared.distribution.DistributeForm,
        submit_label="Record distribution",
        action=action,
        defaults={"package": project, "version": version},
    )
    block.append(form_html)

    return await template.blank(title, content=block.collect())
