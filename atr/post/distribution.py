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

from typing import Final, Literal

import quart

import atr.blueprints.post as post
import atr.db as db
import atr.db.interaction as interaction
import atr.errors as errors
import atr.form as form
import atr.get as get
import atr.models.distribution as distribution
import atr.models.safe as safe
import atr.models.sql as sql
import atr.sessions as sessions
import atr.shared as shared
import atr.storage as storage
import atr.tasks as tasks
import atr.web as web

_AUTOMATED_PLATFORMS: Final[tuple[shared.distribution.DistributionPlatform, ...]] = (
    shared.distribution.DistributionPlatform.MAVEN,
)
_AUTOMATED_PLATFORMS_STAGE: Final[tuple[shared.distribution.DistributionPlatform, ...]] = (
    shared.distribution.DistributionPlatform.MAVEN,
)


async def automate_form_process_page(
    session: web.Committer,
    form_data: shared.distribution.DistributionAutomateForm,
    project: safe.ProjectKey,
    version: safe.VersionKey,
    /,
    staging: bool = False,
) -> web.WerkzeugResponse:
    allowed_platforms = _AUTOMATED_PLATFORMS_STAGE if staging else _AUTOMATED_PLATFORMS
    if form_data.platform not in allowed_platforms:
        platform_str = form_data.platform.value
        await _keep_submitted_values(session, shared.distribution.DistributionAutomateForm)
        return await session.redirect(
            get.distribution.stage_automate if staging else get.distribution.automate,
            project_key=str(project),
            version_key=str(version),
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
    async with storage.write_as_project_release_manager(project, session) as warm:
        try:
            await warm.distributions.automate(
                release.safe_key,
                dd.platform,
                committee.key,
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
            await _keep_submitted_values(session, shared.distribution.DistributionAutomateForm)
            return await session.redirect(
                get.distribution.stage_automate if staging else get.distribution.automate,
                project_key=project,
                version_key=version,
                error=str(e),
            )

    # Success - redirect to distribution list with success message
    message = "Distribution queued successfully."
    return await session.redirect(
        get.distribution.stage_list if staging else get.distribution.list_get,
        project_key=project,
        version_key=version,
        success=message,
    )


@post.typed
async def automate_selected(
    session: web.Committer,
    _distribution_automate: Literal["distribution/automate"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    distribute_form: shared.distribution.DistributionAutomateForm,
) -> web.WerkzeugResponse:
    """
    URL: /distribution/automate/<project_key>/<version_key>
    """
    return await automate_form_process_page(session, distribute_form, project_key, version_key, staging=False)


@post.typed
async def change_version(
    session: web.Committer,
    _distribution_version: Literal["distribution/version"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    change_form: shared.distribution.ChangeVersionForm,
) -> web.WerkzeugResponse:
    """
    URL: /distribution/version/<project_key>/<version_key>
    """
    release_key = await _release_key(project_key, version_key)
    try:
        new_version = safe.VersionKey(change_form.new_version.strip())
    except ValueError as e:
        return await _list_redirect(session, project_key, version_key, error=f"Invalid version: {e}")
    try:
        async with storage.write_as_project_release_manager(project_key, session) as warm:
            await warm.distributions.change_version(
                release_key,
                _sql_platform(change_form.platform),
                change_form.owner_namespace,
                change_form.package,
                change_form.version,
                new_version,
            )
    except storage.AccessError as e:
        return await _list_redirect(session, project_key, version_key, error=str(e))
    await tasks.distribution_check_now(session.uid)
    return await _list_redirect(
        session, project_key, version_key, success=f"Checking for {change_form.package} {new_version}."
    )


@post.typed
async def check_all(
    session: web.Committer,
    _distribution_check: Literal["distribution/check"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    _form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /distribution/check/<project_key>/<version_key>
    """
    release_key = await _release_key(project_key, version_key)
    try:
        async with storage.write_as_project_release_manager(project_key, session) as warm:
            count = await warm.distributions.check_now(release_key)
    except storage.AccessError as e:
        return await _list_redirect(session, project_key, version_key, error=str(e))
    if count == 0:
        return await _list_redirect(session, project_key, version_key, success="Nothing to check.")
    await tasks.distribution_check_now(session.uid)
    return await _list_redirect(session, project_key, version_key, success="Checking now.")


@post.typed
async def delete(
    session: web.Committer,
    _distribution_delete: Literal["distribution/delete"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    delete_form: shared.distribution.DeleteForm,
) -> web.WerkzeugResponse:
    """
    URL: /distribution/delete/<project_key>/<version_key>
    """
    sql_platform = delete_form.platform.to_sql()  # type: ignore[attr-defined]

    async with db.session() as data:
        release = await data.release(project_key=str(project_key), version=str(version_key)).demand(
            RuntimeError(errors.RELEASE_NOT_FOUND)
        )
    if release.safe_key != delete_form.release_key:
        raise RuntimeError("Release name mismatch")

    # Delete the distribution
    async with storage.write_as_project_release_manager(project_key, session) as warm:
        await warm.distributions.delete_distribution(
            release_key=release.safe_key,
            platform=sql_platform,
            owner_namespace=delete_form.owner_namespace,
            package=delete_form.package,
            version=delete_form.version,
        )
    return await session.redirect(
        get.distribution.list_get,
        project_key=str(project_key),
        version_key=str(version_key),
        success="Distribution deleted",
    )


@post.typed
async def ignore(
    session: web.Committer,
    _distribution_ignore: Literal["distribution/ignore"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    ignore_form: shared.distribution.IgnoreForm,
) -> web.WerkzeugResponse:
    """
    URL: /distribution/ignore/<project_key>/<version_key>
    """
    release_key = await _release_key(project_key, version_key)
    try:
        async with storage.write_as_project_release_manager(project_key, session) as warm:
            await warm.distributions.ignore(
                release_key,
                _sql_platform(ignore_form.platform),
                ignore_form.owner_namespace,
                ignore_form.package,
                ignore_form.version,
                ignore_form.reason,
            )
    except storage.AccessError as e:
        return await _list_redirect(session, project_key, version_key, error=str(e))
    return await _list_redirect(
        session, project_key, version_key, success=f"{ignore_form.package} is ignored for this release."
    )


async def record_form_process_page(
    session: web.Committer,
    form_data: shared.distribution.DistributionRecordForm,
    project: safe.ProjectKey,
    version: safe.VersionKey,
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
    release, _committee = await shared.distribution.release_validated_and_committee(
        project,
        version,
        staging=staging,
    )

    async with storage.write_as_project_release_manager(project, session) as warm:
        try:
            _dist, added, _metadata = await warm.distributions.record_from_data(
                release_key=release.safe_key,
                staging=staging,
                dd=dd,
                allow_retries=False,
            )
        except (storage.AccessError, shared.distribution.PlatformNotStageableError) as e:
            # Both are user-input problems, so redirect back to the form with the message
            await _keep_submitted_values(session, shared.distribution.DistributionRecordForm)
            return await session.redirect(
                get.distribution.stage_record if staging else get.distribution.record,
                project_key=str(project),
                version_key=str(version),
                error=str(e),
            )

    # Success - redirect to distribution list with success message
    message = "Distribution recorded successfully." if added else "Distribution was already recorded."
    return await session.redirect(
        get.distribution.stage_list if staging else get.distribution.list_get,
        project_key=str(project),
        version_key=str(version),
        success=message,
    )


@post.typed
async def record_selected(
    session: web.Committer,
    _distribution_record: Literal["distribution/record"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    distribute_form: shared.distribution.DistributionRecordForm,
) -> web.WerkzeugResponse:
    """
    URL: /distribution/record/<project_key>/<version_key>
    """
    return await record_form_process_page(session, distribute_form, project_key, version_key, staging=False)


@post.typed
async def stage_automate_selected(
    session: web.Committer,
    _distribution_stage_automate: Literal["distribution/stage/automate"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    distribute_form: shared.distribution.DistributionAutomateForm,
) -> web.WerkzeugResponse:
    """
    URL: /distribution/stage/automate/<project_key>/<version_key>
    """
    return await automate_form_process_page(session, distribute_form, project_key, version_key, staging=True)


@post.typed
async def stage_record_selected(
    session: web.Committer,
    _distribution_stage_record: Literal["distribution/stage/record"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    distribute_form: shared.distribution.DistributionRecordForm,
) -> web.WerkzeugResponse:
    """
    URL: /distribution/stage/record/<project_key>/<version_key>
    """
    return await record_form_process_page(session, distribute_form, project_key, version_key, staging=True)


@post.typed
async def unignore(
    session: web.Committer,
    _distribution_unignore: Literal["distribution/unignore"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    unignore_form: shared.distribution.UnignoreForm,
) -> web.WerkzeugResponse:
    """
    URL: /distribution/unignore/<project_key>/<version_key>
    """
    release_key = await _release_key(project_key, version_key)
    try:
        async with storage.write_as_project_release_manager(project_key, session) as warm:
            await warm.distributions.unignore(
                release_key,
                _sql_platform(unignore_form.platform),
                unignore_form.owner_namespace,
                unignore_form.package,
                unignore_form.version,
            )
    except storage.AccessError as e:
        return await _list_redirect(session, project_key, version_key, error=str(e))
    await tasks.distribution_check_now(session.uid)
    return await _list_redirect(session, project_key, version_key, success=f"Checking for {unignore_form.package}.")


async def _keep_submitted_values(session: web.Committer, form_cls: type[form.Form]) -> None:
    submitted = await session.form_data()
    await sessions.form_error_put(quart.request.path, form.flash_error_data(form_cls, [], submitted))


async def _list_redirect(
    session: web.Committer,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    *,
    success: str | None = None,
    error: str | None = None,
) -> web.WerkzeugResponse:
    return await session.redirect(
        get.distribution.list_get,
        project_key=str(project_key),
        version_key=str(version_key),
        success=success,
        error=error,
    )


async def _release_key(project_key: safe.ProjectKey, version_key: safe.VersionKey) -> safe.ReleaseKey:
    async with db.session() as data:
        release = await data.release(project_key=str(project_key), version=str(version_key)).demand(
            errors.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404)
        )
    return release.safe_key


def _sql_platform(platform: object) -> sql.DistributionPlatform:
    if not isinstance(platform, shared.distribution.DistributionPlatform):
        raise ValueError("Unknown distribution platform")
    return platform.to_sql()
