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

import asyncio
import urllib.parse
from typing import Final, Literal

import aiohttp
import quart

import atr.blueprints.post as post
import atr.config as config
import atr.construct as construct
import atr.get as get
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web

_DOWNLOAD_PAGE_KEPT: Final = (
    "The project already received a different download page URL, {url}, before this form was submitted."
    " The existing URL has been kept."
)
_DOWNLOAD_PAGE_OVERALL_TIMEOUT: Final = 30
_DOWNLOAD_PAGE_REDIRECT_LIMIT: Final = 3
_DOWNLOAD_PAGE_TIMEOUT: Final = aiohttp.ClientTimeout(total=10, connect=5)
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})


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

    if (committee := release.project.committee) is None:
        raise ValueError("Release has no committee")
    if response := await _validate_recipients(session, announce_form, util.unwrap(committee.key), release.project):
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

    try:
        async with storage.write_as_project_release_manager(project_key, session) as warm:
            if response := await _validate_download_page(session, release, announce_form):
                return response
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
        async with asyncio.timeout(_DOWNLOAD_PAGE_OVERALL_TIMEOUT):
            async with util.create_secure_session(timeout=_DOWNLOAD_PAGE_TIMEOUT, public=True) as http_session:
                return await _download_page_fetch_error(http_session, url)
    except (aiohttp.ClientError, TimeoutError) as e:
        return f"The download page URL could not be checked: {e}"


async def _download_page_fetch_error(http_session: aiohttp.ClientSession, url: str) -> str | None:
    for _ in range(_DOWNLOAD_PAGE_REDIRECT_LIMIT + 1):
        if error := util.download_page_url_error(url):
            return f"The download page URL check led to {url}: {error}"
        async with http_session.get(url, allow_redirects=False) as response:
            location = response.headers.get("Location")
            if (response.status in _REDIRECT_STATUSES) and location:
                url = urllib.parse.urljoin(str(response.url), location)
                continue
            if response.status == 200:
                return None
            return f"The download page URL returned HTTP {response.status}, not 200"
    return "The download page URL used too many redirects"


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
    policy = release.release_policy or release.project.release_policy
    if not (policy and policy.file_tag_mappings):
        return None
    published = [d.platform.value.gh_slug for d in release.distributions if (not d.staging) and (not d.pending)]
    missing = [tag for tag in policy.file_tag_mappings if tag not in published]
    if not missing:
        return None
    return await session.redirect(
        get.announce.selected,
        error=f"This release cannot be announced until the following distributions have been recorded: {
            ', '.join(missing)
        }",
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
    committee_key: str,
    project: sql.Project,
) -> web.WerkzeugResponse | None:
    permitted = util.permitted_announce_recipients(session.uid, committee_key=committee_key, project=project)
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
