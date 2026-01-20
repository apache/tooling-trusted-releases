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

from __future__ import annotations

from typing import Final

import atr.blueprints.post as post
import atr.db as db
import atr.db.interaction as interaction
import atr.get as get
import atr.models.distribution as distribution
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.web as web

_AUTOMATED_PLATFORMS: Final[tuple[shared.distribution.DistributionPlatform, ...]] = (
    shared.distribution.DistributionPlatform.MAVEN,
)
_AUTOMATED_PLATFORMS_STAGE: Final[tuple[shared.distribution.DistributionPlatform, ...]] = (
    shared.distribution.DistributionPlatform.MAVEN,
)


async def automate_form_process_page(
    session: web.Committer,
    form_data: shared.distribution.DistributeForm,
    project: str,
    version: str,
    /,
    staging: bool = False,
) -> web.WerkzeugResponse:
    allowed_platforms = _AUTOMATED_PLATFORMS_STAGE if staging else _AUTOMATED_PLATFORMS
    if form_data.platform not in allowed_platforms:
        platform_str = form_data.platform.value
        return await session.redirect(
            get.distribution.stage_automate if staging else get.distribution.automate,
            project=project,
            version=version,
            error=f"Platform {platform_str} is not supported for automated distribution",
        )
    sql_platform = form_data.platform.to_sql()  # type: ignore[attr-defined]
    dd = distribution.Data(
        platform=sql_platform,
        owner_namespace=form_data.owner_namespace,
        package=form_data.package,
        version=form_data.version,
        details=form_data.details,
    )
    release, committee = await shared.distribution.release_validated_and_committee(
        project, version, staging=staging, release_policy=True
    )
    phase = interaction.TrustedProjectPhase.COMPOSE
    match release.phase:
        case sql.ReleasePhase.RELEASE_PREVIEW:
            phase = interaction.TrustedProjectPhase.FINISH
        case sql.ReleasePhase.RELEASE_CANDIDATE:
            phase = interaction.TrustedProjectPhase.VOTE
        case sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            phase = interaction.TrustedProjectPhase.COMPOSE
    async with storage.write_as_committee_member(committee_name=committee.name) as w:
        try:
            await w.distributions.automate(
                release.name,
                dd.platform,
                committee.name,
                dd.owner_namespace,
                project,
                version,
                phase.value,
                release.latest_revision_number,
                dd.package,
                dd.version,
                staging,
            )
        except storage.AccessError as e:
            # Instead of calling record_form_page_new, redirect with error message
            return await session.redirect(
                get.distribution.stage_automate if staging else get.distribution.automate,
                project=project,
                version=version,
                error=str(e),
            )

    # Success - redirect to distribution list with success message
    message = "Distribution queued successfully."
    return await session.redirect(
        get.distribution.list_get if staging else get.finish.selected,
        project_name=project,
        version_name=version,
        success=message,
    )


@post.committer("/distribution/automate/<project>/<version>")
@post.form(shared.distribution.DistributeForm)
async def automate_selected(
    session: web.Committer, distribute_form: shared.distribution.DistributeForm, project: str, version: str
) -> web.WerkzeugResponse:
    return await automate_form_process_page(session, distribute_form, project, version, staging=False)


@post.committer("/distribution/delete/<project>/<version>")
@post.form(shared.distribution.DeleteForm)
async def delete(
    session: web.Committer, delete_form: shared.distribution.DeleteForm, project: str, version: str
) -> web.WerkzeugResponse:
    sql_platform = delete_form.platform.to_sql()  # type: ignore[attr-defined]

    # Validate the submitted data, and obtain the committee for its name
    async with db.session() as data:
        release = await data.release(name=delete_form.release_name).demand(
            RuntimeError(f"Release {delete_form.release_name} not found")
        )
        committee = release.committee
        if committee is None:
            raise RuntimeError(f"Release {delete_form.release_name} has no committee")

    # Delete the distribution
    async with storage.write_as_committee_member(committee_name=committee.name) as wacm:
        await wacm.distributions.delete_distribution(
            release_name=delete_form.release_name,
            platform=sql_platform,
            owner_namespace=delete_form.owner_namespace,
            package=delete_form.package,
            version=delete_form.version,
        )
    return await session.redirect(
        get.distribution.list_get,
        project_name=project,
        version_name=version,
        success="Distribution deleted",
    )


async def record_form_process_page(
    session: web.Committer,
    form_data: shared.distribution.DistributeForm,
    project: str,
    version: str,
    /,
    staging: bool = False,
) -> web.WerkzeugResponse:
    sql_platform = form_data.platform.to_sql()  # type: ignore[attr-defined]
    dd = distribution.Data(
        platform=sql_platform,
        owner_namespace=form_data.owner_namespace,
        package=form_data.package,
        version=form_data.version,
        details=form_data.details,
    )
    release, committee = await shared.distribution.release_validated_and_committee(
        project,
        version,
        staging=staging,
    )

    async with storage.write_as_committee_member(committee_name=committee.name) as w:
        try:
            _dist, added, _metadata = await w.distributions.record_from_data(
                release_name=release.name,
                staging=staging,
                dd=dd,
            )
        except storage.AccessError as e:
            # Instead of calling record_form_page_new, redirect with error message
            return await session.redirect(
                get.distribution.stage_record if staging else get.distribution.record,
                project=project,
                version=version,
                error=str(e),
            )

    # Success - redirect to distribution list with success message
    message = "Distribution recorded successfully." if added else "Distribution was already recorded."
    return await session.redirect(
        get.distribution.list_get,
        project_name=project,
        version_name=version,
        success=message,
    )


@post.committer("/distribution/record/<project>/<version>")
@post.form(shared.distribution.DistributeForm)
async def record_selected(
    session: web.Committer, distribute_form: shared.distribution.DistributeForm, project: str, version: str
) -> web.WerkzeugResponse:
    return await record_form_process_page(session, distribute_form, project, version, staging=False)


@post.committer("/distribution/stage/automate/<project>/<version>")
@post.form(shared.distribution.DistributeForm)
async def stage_automate_selected(
    session: web.Committer, distribute_form: shared.distribution.DistributeForm, project: str, version: str
) -> web.WerkzeugResponse:
    return await automate_form_process_page(session, distribute_form, project, version, staging=True)


@post.committer("/distribution/stage/record/<project>/<version>")
@post.form(shared.distribution.DistributeForm)
async def stage_record_selected(
    session: web.Committer, distribute_form: shared.distribution.DistributeForm, project: str, version: str
) -> web.WerkzeugResponse:
    return await record_form_process_page(session, distribute_form, project, version, staging=True)
