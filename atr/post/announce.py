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

from typing import Literal

import atr.blueprints.post as post
import atr.construct as construct
import atr.form as form
import atr.get as get
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web


class PreviewForm(form.Form):
    download_path_suffix: safe.OptionalRelPath = form.label("Download path suffix")


@post.typed
async def preview(
    session: web.Committer,
    _announce_preview: Literal["announce/preview"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    preview_form: PreviewForm,
) -> web.QuartResponse:
    """
    URL: /announce/preview/<project_key>/<version_key>/<revision_number>
    """
    default_body_template = await construct.announce_release_default(project_key)
    options = construct.AnnounceReleaseOptions(
        asfuid=session.uid,
        fullname=session.fullname,
        project_key=project_key,
        version_key=version_key,
        revision_number=revision_number,
        download_path_suffix=preview_form.download_path_suffix,
    )
    _, body = await construct.announce_release_subject_and_body("", default_body_template, options)
    return web.TextResponse(body)


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
            await warm.announce.release(
                project_key=project_key,
                version_key=version_key,
                preview_revision_number=preview_revision_number,
                email_to=announce_form.email_to,
                body=announce_form.body,
                download_path_suffix=announce_form.download_path_suffix,
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

    routes_release_finished = get.release.finished
    return await session.redirect(
        routes_release_finished,
        success="Preview successfully announced",
        project_key=str(project_key),
    )


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
