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

import datetime
from typing import TYPE_CHECKING, Final, Literal

import sqlalchemy.exc

import atr.blueprints.post as blueprints_post
import atr.config as config
import atr.constants as constants
import atr.cycles as cycles
import atr.db as db
import atr.get as get
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@blueprints_post.typed
async def post(
    session: web.Committer,
    _file: Literal["file"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_form: shared.projects.FileViewForm,
) -> web.WerkzeugResponse:
    """
    URL: /file/<project_key>/<version_key>
    """
    return await _FORM_HANDLERS[type(file_form)](session, project_key, version_key)


async def _archive_release(
    session: web.Committer, project_key: safe.ProjectKey, release_version: safe.VersionKey
) -> web.WerkzeugResponse:
    # The writer decides eligibility under the write lock, so a release can't become the
    # latest in its cycle, or gain an archival vote, between the check and the archival
    async with storage.write(session) as write:
        try:
            wacm = await write.as_project_committee_member(project_key)
            await wacm.release.archive(project_key, release_version)
        except storage.AccessError as e:
            return await _redirect_to_release(session, project_key, release_version, error=f"Error archiving: {e}")

    return await _redirect_to_release(
        session, project_key, release_version, success=f"Release {project_key} {release_version} archived."
    )


async def _persist_release_approval(
    session: web.Committer,
    wacm: storage.WriteAsCommitteeMember,
    project_key: safe.ProjectKey,
    release_version: safe.VersionKey,
    committee_key: str,
    cap_question_id: int,
    closes_at: datetime.datetime,
) -> web.WerkzeugResponse:
    try:
        await wacm.project.request_approval(
            project_key,
            sql.ApprovalAction.ARCHIVE_RELEASE,
            cap_question_id,
            closes_at,
            release_version=release_version,
        )
    except storage.AccessError as e:
        log.warning(f"Concurrent CAP request for {project_key} lost. CAP question {cap_question_id} abandoned")
        return await _redirect_to_release(session, project_key, release_version, error=str(e))
    except sqlalchemy.exc.SQLAlchemyError:
        log.exception(f"Failed to record CAP approval for {project_key}. CAP question {cap_question_id} abandoned")
        return await _redirect_to_release(
            session,
            project_key,
            release_version,
            error="Could not record the CAP approval vote. Please contact an administrator.",
        )

    return await _redirect_to_release(
        session,
        project_key,
        release_version,
        success=(
            f"Approval vote created for the {committee_key} PMC (CAP #{cap_question_id}, closes "
            f"{closes_at.strftime('%Y-%m-%d %H:%M UTC')}). ATR will auto-archive it if the vote passes."
        ),
    )


async def _redirect_to_release(
    session: web.Committer,
    project_key: safe.ProjectKey,
    release_version: safe.VersionKey,
    **kwargs: str,
) -> web.WerkzeugResponse:
    return await session.redirect(
        get.file.selected, project_key=str(project_key), version_key=str(release_version), **kwargs
    )


def _release_archival_error(
    project: sql.Project, release: sql.Release | None, release_version: safe.VersionKey
) -> str | None:
    if release is None:
        return f"Release {project.key} {release_version} not found."
    if release.phase != sql.ReleasePhase.RELEASE:
        return f"Release {project.key} {release_version} is not a full release."
    if release.is_archived:
        return f"Release {project.key} {release_version} is already archived."
    return None


async def _request_archival_vote(
    session: web.Committer, project_key: safe.ProjectKey, release_version: safe.VersionKey
) -> web.WerkzeugResponse:
    if not config.get().CAP_ROLE_ACCOUNT_TOKEN:
        return await _redirect_to_release(
            session,
            project_key,
            release_version,
            error="CAP approval is not configured. Please contact an administrator.",
        )

    async with storage.write(session) as write:
        try:
            wacm = await write.as_project_committee_member(project_key)
        except storage.AccessError as e:
            return await _redirect_to_release(
                session, project_key, release_version, error=f"Error requesting archival approval: {e}"
            )

        async with db.session() as data:
            project = await data.project(key=str(project_key), _committee=True, _releases=True).get()
            if (project is None) or (project.committee is None):
                return await session.redirect(get.projects.projects, error=f"Project '{project_key}' not found.")
            committee_key = project.committee.key
            display_name = project.display_name
            release = await data.release(project_key=str(project_key), version=str(release_version)).get()
            releases = project.releases_including_embargoed
            error = _release_archival_error(project, release, release_version)
            latest = (release is None) or cycles.is_latest_in_cycle(project, release, releases)
            if (error is None) and (not latest):
                error = (
                    f"Release {project_key} {release_version} is not the latest in its cycle,"
                    " so it can be archived directly without a CAP approval vote."
                )
            if error is not None:
                return await _redirect_to_release(session, project_key, release_version, error=error)
            existing = await data.approval_request(
                project_key=str(project_key),
                status_in=[sql.ApprovalStatus.PENDING, sql.ApprovalStatus.APPROVED],
                release_version=str(release_version),
            ).get()
            if existing is not None:
                return await _redirect_to_release(
                    session,
                    project_key,
                    release_version,
                    error="A CAP approval request for this release is already in progress.",
                )

        closes_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            minutes=constants.CAP_VOTE_DURATION_MINUTES
        )
        try:
            question = await util.cap_create_approval_question(
                sql.ApprovalAction.ARCHIVE_RELEASE,
                project_key,
                display_name,
                committee_key,
                session.uid,
                closes_at,
                release_version=release_version,
            )
        except util.FetchError as e:
            return await _redirect_to_release(
                session, project_key, release_version, error=f"Could not create CAP approval vote: {e}"
            )

        return await _persist_release_approval(
            session, wacm, project_key, release_version, committee_key, question.question_id, closes_at
        )


_FORM_HANDLERS: Final[
    dict[type, Callable[[web.Committer, safe.ProjectKey, safe.VersionKey], Awaitable[web.WerkzeugResponse]]]
] = {
    shared.projects.ArchiveSelectedRelease: _request_archival_vote,
    shared.projects.ConfirmReleaseArchival: _archive_release,
}
