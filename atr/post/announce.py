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

import htpy
import quart

import atr.blueprints.post as post
import atr.config as config
import atr.construct as construct
import atr.errors as errors
import atr.form as form
import atr.get as get
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.strings as strings
import atr.util as util
import atr.web as web

_DOWNLOAD_PAGE_KEPT: Final = (
    "The project already received a different download page URL, {url}, before this form was submitted."
    " The existing URL has been kept."
)
_PUBLICATION_HELP_URL: Final = "/docs/troubleshooting#files-missing-at-announcement"


@post.typed
async def queue(
    session: web.Committer,
    _announce: Literal["announce/queue"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    announce_form: shared.announce.AnnounceForm,
) -> web.WerkzeugResponse:
    """
    URL: /announce/queue/<project_key>/<version_key>
    Queue the announcement to be sent once the release can be announced.
    """
    release = await session.release(
        project_key,
        version_key,
        with_committee=True,
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        with_release_policy=True,
        with_project_release_policy=True,
    )
    if release.project.committee is None:
        raise ValueError(errors.RELEASE_NO_COMMITTEE)
    if response := await _validate_recipients(session, announce_form, release.project):
        return response
    if response := await _validate_subject_template_hash(session, project_key, announce_form):
        return response
    if response := await _validate_download_page(session, release, announce_form):
        return response
    try:
        async with storage.write_as_project_release_manager(project_key, session) as warm:
            await _record_download_page(warm, project_key, release.project.download_page, announce_form)
            await warm.announce.queue(
                project_key=project_key,
                version_key=version_key,
                revision_number=announce_form.revision_number,
                email_to=announce_form.email_to,
                body=announce_form.body,
                fullname=session.fullname,
                subject_template_hash=announce_form.subject_template_hash,
                email_cc=announce_form.email_cc,
                email_bcc=announce_form.email_bcc,
                auto_archive_prior=announce_form.auto_archive,
            )
    except storage.AccessError as e:
        return await session.redirect(
            get.announce.selected, error=str(e), project_key=str(project_key), version_key=str(version_key)
        )
    return await session.redirect(
        get.finish.selected,
        success="Announcement queued. ATR will send it once the release can be announced.",
        project_key=str(project_key),
        version_key=str(version_key),
    )


@post.typed
async def queue_cancel(
    session: web.Committer,
    _announce: Literal["announce/queue/cancel"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    _form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /announce/queue/cancel/<project_key>/<version_key>
    Cancel the announcement queued for the release.
    """
    try:
        async with storage.write_as_project_release_manager(project_key, session) as warm:
            await warm.announce.queue_cancel(project_key, version_key)
    except storage.AccessError as e:
        return await session.redirect(
            get.finish.selected, error=str(e), project_key=str(project_key), version_key=str(version_key)
        )
    return await session.redirect(
        get.finish.selected,
        success="Queued announcement cancelled",
        project_key=str(project_key),
        version_key=str(version_key),
    )


@post.typed
async def selected(
    session: web.Committer,
    _announce: Literal["announce"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    announce_form: shared.announce.AnnounceForm,
) -> web.WerkzeugResponse:
    """
    URL: /announce/<project_key>/<version_key>
    Handle the announcement form submission and promote the preview to release.
    """
    release = await session.release(
        project_key,
        version_key,
        with_committee=True,
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        with_distributions=True,
        with_release_policy=True,
        with_project_release_policy=True,
    )
    preview_revision_number = release.safe_latest_revision_number

    if release.project.committee is None:
        raise ValueError(errors.RELEASE_NO_COMMITTEE)
    if response := await _validate_recipients(session, announce_form, release.project):
        return response

    if announce_form.revision_number != preview_revision_number:
        return await session.redirect(
            get.announce.selected,
            error=f"The release has been updated since you loaded the form. "
            f"Please review the current revision ({preview_revision_number!s}) and submit the form again.",
            project_key=str(project_key),
            version_key=str(version_key),
        )

    if response := await _validate_distributions(session, release, project_key, version_key):
        return response
    if response := await _validate_subject_template_hash(session, project_key, announce_form):
        return response
    if response := await _validate_download_page(session, release, announce_form):
        return response

    try:
        async with storage.write_as_project_release_manager(project_key, session) as warm:
            await _record_download_page(warm, project_key, release.project.download_page, announce_form)
            await warm.announce.release(
                project_key=project_key,
                version_key=version_key,
                preview_revision_number=preview_revision_number,
                email_to=announce_form.email_to,
                body=announce_form.body,
                fullname=session.fullname,
                subject_template_hash=announce_form.subject_template_hash,
                email_cc=announce_form.email_cc,
                email_bcc=announce_form.email_bcc,
                acknowledge_unreachable=announce_form.announce_unreachable,
                auto_archive_prior=announce_form.auto_archive,
            )
    except storage.PublicationMissingError as e:
        help_link = htpy.a(href=_PUBLICATION_HELP_URL, target="_blank", rel="noopener")[strings.HOW_TO_FIX_THIS]
        await web.flash_error(htpy.span[str(e), " ", help_link])
        return await session.redirect(get.announce.selected, project_key=str(project_key), version_key=str(version_key))
    except storage.PropagationUnreachableError as e:
        return await session.redirect(
            get.announce.selected,
            error=f"{e} To announce anyway, tick the confirmation checkbox now shown in the form and submit it again.",
            project_key=str(project_key),
            version_key=str(version_key),
            unreachable="true",
        )
    except storage.AccessError as e:
        return await session.redirect(
            get.announce.selected, error=str(e), project_key=str(project_key), version_key=str(version_key)
        )

    routes_release_finished = get.release.finished
    return await session.redirect(
        routes_release_finished,
        success="Preview successfully announced",
        project_key=str(project_key),
    )


async def _download_page_error(url: str) -> str | None:
    if config.is_dev_environment():
        return None
    try:
        await util.download_page_fetch(url, read_page=False)
    except util.FetchError as e:
        return str(e)
    return None


async def _record_download_page(
    warm: storage.WriteAsReleaseManager,
    project_key: safe.ProjectKey,
    stored: str | None,
    announce_form: shared.announce.AnnounceForm,
) -> None:
    if stored or (announce_form.download_page is None):
        return
    download_page = str(announce_form.download_page)
    existing = await warm.project.set_download_page(project_key, download_page)
    if existing is None:
        return
    if util.normalized_url(download_page) != util.normalized_url(existing):
        await quart.flash(_DOWNLOAD_PAGE_KEPT.format(url=existing), "warning")


async def _validate_distributions(
    session: web.Committer,
    release: sql.Release,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.WerkzeugResponse | None:
    message = shared.distribution.outstanding_message(release.distributions)
    if not message:
        return None
    return await session.redirect(
        get.announce.selected,
        error=message,
        project_key=str(project_key),
        version_key=str(version_key),
    )


async def _validate_download_page(
    session: web.Committer,
    release: sql.Release,
    announce_form: shared.announce.AnnounceForm,
) -> web.WerkzeugResponse | None:
    download_page = str(announce_form.download_page) if (announce_form.download_page is not None) else None
    stored = release.project.download_page
    if stored:
        if (download_page is not None) and (util.normalized_url(download_page) != util.normalized_url(stored)):
            await quart.flash(_DOWNLOAD_PAGE_KEPT.format(url=stored), "warning")
        return None
    if download_page is None:
        return await session.form_error("download_page", "Enter the URL of the project download page")
    if error := await _download_page_error(download_page):
        return await session.form_error("download_page", error)
    return None


async def _validate_recipients(
    session: web.Committer,
    announce_form: shared.announce.AnnounceForm,
    project: sql.Project,
) -> web.WerkzeugResponse | None:
    permitted = util.permitted_announce_recipients(session.uid, committee=project.committee, project=project)
    addresses = [announce_form.email_to, *announce_form.email_cc, *announce_form.email_bcc]
    for addr in addresses:
        if addr not in permitted:
            return await session.form_error(
                "email_to",
                f"You are not permitted to send announcements to {addr}",
            )
    return None


async def _validate_subject_template_hash(
    session: web.Committer,
    project_key: safe.ProjectKey,
    announce_form: shared.announce.AnnounceForm,
) -> web.WerkzeugResponse | None:
    subject_template = await construct.announce_release_subject_default(project_key)
    if construct.template_hash(subject_template) == announce_form.subject_template_hash:
        return None
    return await session.form_error(
        "subject_template_hash",
        "The subject template has been modified since you loaded the form. Please reload and try again.",
    )
