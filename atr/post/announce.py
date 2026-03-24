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
import atr.get as get
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web


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
    permitted_recipients = util.permitted_announce_recipients(session.uid)

    # Validate that the recipients are permitted
    all_addrs = [announce_form.email_to, *announce_form.email_cc, *announce_form.email_bcc]
    for addr in all_addrs:
        if addr not in permitted_recipients:
            return await session.form_error(
                "email_to",
                f"You are not permitted to send announcements to {addr}",
            )

    # Get the release to find the revision number
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

    # Validate that the revision number matches
    if announce_form.revision_number != preview_revision_number:
        return await session.redirect(
            get.announce.selected,
            error=f"The release has been updated since you loaded the form. "
            f"Please review the current revision ({preview_revision_number!s}) and submit the form again.",
            project_key=str(project_key),
            version_key=str(version_key),
        )

    policy = release.release_policy or release.project.release_policy
    if policy and policy.file_tag_mappings:
        missing = []
        tags = policy.file_tag_mappings.keys()
        distributions = [d.platform.value.gh_slug for d in release.distributions if (not d.staging) and (not d.pending)]
        for tag in tags:
            if tag not in distributions:
                missing.append(tag)
        if missing:
            return await session.redirect(
                get.announce.selected,
                error=f"This release cannot be announced until the following distributions have been recorded: {
                    ', '.join(missing)
                }",
                project_key=str(project_key),
                version_key=str(version_key),
            )

    # Validate that the subject template hasn't changed
    subject_template = await construct.announce_release_subject_default(project_key)
    current_hash = construct.template_hash(subject_template)
    if current_hash != announce_form.subject_template_hash:
        return await session.form_error(
            "subject_template_hash",
            "The subject template has been modified since you loaded the form. Please reload and try again.",
        )

    try:
        async with storage.write_as_project_committee_member(project_key, session) as wacm:
            await wacm.announce.release(
                project_key=project_key,
                version_key=version_key,
                preview_revision_number=preview_revision_number,
                email_to=announce_form.email_to,
                body=announce_form.body,
                download_path_suffix=announce_form.download_path_suffix,
                asf_uid=session.uid,
                fullname=session.fullname,
                subject_template_hash=announce_form.subject_template_hash,
                email_cc=announce_form.email_cc,
                email_bcc=announce_form.email_bcc,
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
