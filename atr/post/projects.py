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
from typing import TYPE_CHECKING, Any, Final, Literal

import quart
import sqlalchemy.exc

import atr.blueprints.post as post
import atr.config as config
import atr.constants as constants
import atr.db as db
import atr.get as get
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.shared as shared
import atr.storage as storage
import atr.tasks.cap as cap
import atr.util as util
import atr.web as web

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@post.typed
async def add_project(
    session: web.Committer,
    _project_add: Literal["project/add"],
    committee_key: safe.CommitteeKey,
    project_form: shared.projects.AddProjectForm,
) -> web.WerkzeugResponse:
    """
    URL: /project/add/<committee_key>
    """
    display_name = project_form.display_name
    project_key = project_form.key

    if committee_key != project_form.committee_key:
        raise ValueError(f"Invalid committee key: {committee_key}")

    async with storage.write(session) as write:
        wacm = write.as_committee_member(str(committee_key))
        try:
            await wacm.project.create(display_name, project_key)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.add_project, committee_key=str(committee_key), error=f"Error adding project: {e}"
            )

    return await session.redirect(
        get.projects.view, project_key=str(project_key), success=f"Project '{display_name}' added successfully."
    )


@post.typed
async def archive(
    session: web.Committer,
    _project_archive: Literal["project/archive"],
    archive_selected_project_form: shared.projects.ArchiveSelectedProject,
) -> web.WerkzeugResponse:
    """
    URL: /project/archive
    """
    return await _request_approval(session, archive_selected_project_form.project_key, sql.ApprovalAction.ARCHIVE)


@post.typed
async def complete_approval(
    session: web.Committer,
    _project_complete_approval: Literal["project/complete-approval"],
    complete_approval_request_form: shared.projects.CompleteApprovalRequest,
) -> web.WerkzeugResponse:
    """
    URL: /project/complete-approval
    """
    approval_request_id = complete_approval_request_form.approval_request_id
    async with db.session() as data:
        approval = await data.approval_request(id=approval_request_id).get()
    if approval is None:
        return await session.redirect(get.projects.projects, error="Approval request not found.")
    if approval.status != sql.ApprovalStatus.APPROVED:
        return await session.redirect(get.projects.projects, error="This approval request is not ready to complete.")
    if approval.action == sql.ApprovalAction.ARCHIVE_RELEASE:
        # Release archival completes on its own once the vote passes, so there's
        # nothing to do here by hand
        return await session.redirect(
            get.projects.projects,
            error="Release archival completes automatically once the CAP vote passes.",
        )

    project_key = safe.ProjectKey(approval.project_key)
    async with db.session() as data:
        project = await data.project(key=str(project_key), _releases=True).get()
        if (project is None) or (project.committee is None):
            return await session.redirect(get.projects.projects, error=f"Project '{project_key}' not found.")
        active_siblings = len(
            await data.project(
                committee_key=project.committee.key, status=sql.ProjectStatus.ACTIVE, _committee=False
            ).all()
        )
        eligibility_error = _action_eligibility_error(
            project, approval.action, active_siblings, completing=True, requested_at=approval.requested_at
        )
        if eligibility_error is not None:
            return await session.redirect(get.projects.projects, error=eligibility_error)
        draft_versions = [
            safe.VersionKey(r.version)
            for r in project.releases_including_embargoed
            if r.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
        ]

    try:
        await _complete_action(session, approval.action, project_key, draft_versions, approval_request_id)
    except storage.AccessError as e:
        return await session.redirect(get.projects.projects, error=f"Error completing {approval.action.value}: {e}")

    await cap.notify(
        approval.requested_by,
        f"Project {project_key} was {approval.action.value}d after CAP approval.",
        sql.NotificationLevel.INFO,
    )
    return await session.redirect(
        get.projects.projects, success=f"Project '{project_key}' {approval.action.value}d successfully."
    )


@post.typed
async def delete(
    session: web.Committer,
    _project_delete: Literal["project/delete"],
    delete_selected_project_form: shared.projects.DeleteSelectedProject,
) -> web.WerkzeugResponse:
    """
    URL: /project/delete
    """
    return await _request_approval(session, delete_selected_project_form.project_key, sql.ApprovalAction.DELETE)


@post.typed
async def view(
    session: web.Committer,
    _projects: Literal["projects"],
    name: unsafe.UnsafeStr,
    project_form: shared.projects.ProjectViewForm,
) -> web.WerkzeugResponse:
    """
    URL: /projects/<name>
    """
    return await _VIEW_HANDLERS[type(project_form)](session, project_form)


def _action_eligibility_error(
    project: sql.Project,
    action: sql.ApprovalAction,
    active_sibling_count: int,
    *,
    completing: bool = False,
    requested_at: datetime.datetime | None = None,
) -> str | None:
    if not project.is_active:
        return f"Project '{project.key}' is not active."
    if active_sibling_count <= 1:
        return f"Project '{project.key}' is the only active project in its committee."
    releases = project.releases_including_embargoed
    if action == sql.ApprovalAction.DELETE:
        if releases:
            return f"Project '{project.key}' has releases and cannot be deleted."
        return None
    if (not completing) and (not releases):
        return f"Project '{project.key}' has no releases to archive."
    if not all(r.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT for r in releases):
        return f"Project '{project.key}' has non-draft releases and cannot be archived."
    if not completing:
        return None
    new_drafts = [r for r in releases if (requested_at is None) or (r.created > requested_at)]
    if new_drafts:
        return (
            f"Project '{project.key}' has {util.plural(len(new_drafts), 'draft release')} created after the"
            " approval vote was requested. Delete them before completing the archival."
        )
    return None


async def _complete_action(
    session: web.Committer,
    action: sql.ApprovalAction,
    project_key: safe.ProjectKey,
    draft_versions: list[safe.VersionKey],
    approval_request_id: int,
) -> None:
    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        if action == sql.ApprovalAction.DELETE:
            await wacm.project.delete(project_key, approval_request_id)
            return
        for version in draft_versions:
            try:
                await wacm.release.delete(project_key, version)
            except storage.AccessError as e:
                if e.status != 404:
                    raise
        await wacm.project.archive(project_key, approval_request_id)


async def _metadata_category_add(
    wacm: storage.WriteAsCommitteeMember, project_key: safe.ProjectKey, category_to_add: str
) -> bool:
    try:
        return await wacm.project.category_add(project_key, category_to_add.strip())
    except storage.AccessError as e:
        await quart.flash(f"Error adding category: {e}", "error")
        return False


async def _metadata_category_remove(
    wacm: storage.WriteAsCommitteeMember, project_key: safe.ProjectKey, action_value: str
) -> bool:
    try:
        return await wacm.project.category_remove(project_key, action_value)
    except storage.AccessError as e:
        await quart.flash(f"Error removing category: {e}", "error")
        return False


async def _metadata_language_add(
    wacm: storage.WriteAsCommitteeMember, project_key: safe.ProjectKey, language_to_add: str
) -> bool:
    try:
        return await wacm.project.language_add(project_key, language_to_add)
    except storage.AccessError as e:
        await quart.flash(f"Error adding language: {e}", "error")
        return False


async def _metadata_language_remove(
    wacm: storage.WriteAsCommitteeMember, project_key: safe.ProjectKey, action_value: str
) -> bool:
    try:
        return await wacm.project.language_remove(project_key, action_value)
    except storage.AccessError as e:
        await quart.flash(f"Error removing language: {e}", "error")
        return False


async def _persist_approval(
    session: web.Committer,
    wacm: storage.WriteAsCommitteeMember,
    project_key: safe.ProjectKey,
    committee_key: str,
    action: sql.ApprovalAction,
    cap_question_id: int,
    closes_at: datetime.datetime,
) -> web.WerkzeugResponse:
    try:
        await wacm.project.request_approval(project_key, action, cap_question_id, closes_at)
    except storage.AccessError as e:
        log.warning(f"Concurrent CAP request for {project_key} lost. CAP question {cap_question_id} abandoned")
        return await session.redirect(get.projects.projects, error=str(e))
    except sqlalchemy.exc.SQLAlchemyError:
        log.exception(f"Failed to record CAP approval for {project_key}. CAP question {cap_question_id} abandoned")
        return await session.redirect(
            get.projects.projects, error="Could not record the CAP approval vote. Please contact an administrator."
        )

    return await session.redirect(
        get.projects.projects,
        success=(
            f"Approval vote created for the {committee_key} PMC (CAP #{cap_question_id}, closes "
            f"{closes_at.strftime('%Y-%m-%d %H:%M UTC')}). ATR will mark it ready to complete once it passes."
        ),
    )


async def _process_add_category(
    session: web.Committer, add_category_form: shared.projects.AddCategoryForm
) -> web.WerkzeugResponse:
    project_key = add_category_form.project_key
    category_to_add = add_category_form.category_to_add.strip()

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        modified = await _metadata_category_add(wacm, project_key, category_to_add)

    if modified:
        return await session.redirect(
            get.projects.view,
            project_key=str(project_key),
            tab="metadata",
            success=f"Category '{category_to_add}' added.",
        )
    return await session.redirect(
        get.projects.view,
        project_key=str(project_key),
        tab="metadata",
        error=f"Category '{category_to_add}' already exists.",
    )


async def _process_add_language(
    session: web.Committer, add_language_form: shared.projects.AddLanguageForm
) -> web.WerkzeugResponse:
    project_key = add_language_form.project_key
    language_to_add = add_language_form.language_to_add.strip()

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        modified = await _metadata_language_add(wacm, project_key, language_to_add)

    if modified:
        return await session.redirect(
            get.projects.view,
            project_key=str(project_key),
            tab="metadata",
            success=f"Language '{language_to_add}' added.",
        )
    return await session.redirect(
        get.projects.view,
        project_key=str(project_key),
        tab="metadata",
        error=f"Language '{language_to_add}' already exists.",
    )


async def _process_compose_form(
    session: web.Committer, compose_form: shared.projects.ComposePolicyForm
) -> web.WerkzeugResponse:
    project_key = compose_form.project_key

    async with storage.write(session) as write:
        warm = await write.as_project_release_manager(project_key)
        try:
            await warm.policy.edit_compose(compose_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=project_key,
                tab="compose",
                error=f"Error editing compose policy: {e}",
            )

    return await session.redirect(
        get.projects.view,
        project_key=project_key,
        tab="compose",
        success="Compose options saved successfully.",
    )


async def _process_edit_cycle_dates_form(
    session: web.Committer, edit_form: shared.projects.EditCycleDatesForm
) -> web.WerkzeugResponse:
    project_key = edit_form.project_key

    async with storage.write(session) as write:
        warm = await write.as_project_release_manager(project_key)
        try:
            await warm.policy.edit_cycle_dates(edit_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=str(project_key),
                tab="releases",
                error=f"Error saving cycle dates: {e}",
            )
        except ValueError as e:
            return await session.redirect(get.projects.view, project_key=str(project_key), tab="releases", error=str(e))

    return await session.redirect(
        get.projects.view, project_key=str(project_key), tab="releases", success="Cycle dates saved."
    )


async def _process_edit_metadata_form(
    session: web.Committer, edit_form: shared.projects.EditMetadataForm
) -> web.WerkzeugResponse:
    project_key = edit_form.project_key

    async with storage.write(session) as write:
        warm = await write.as_project_release_manager(project_key)
        try:
            await warm.project.edit_metadata(edit_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=str(project_key),
                tab="metadata",
                error=f"Error saving metadata: {e}",
            )
        except ValueError as e:
            return await session.redirect(get.projects.view, project_key=str(project_key), tab="metadata", error=str(e))

    return await session.redirect(
        get.projects.view, project_key=str(project_key), tab="metadata", success="Metadata saved."
    )


async def _process_edit_version_scheme_form(
    session: web.Committer, edit_form: shared.projects.EditVersionSchemeForm
) -> web.WerkzeugResponse:
    project_key = edit_form.project_key

    async with storage.write(session) as write:
        warm = await write.as_project_release_manager(project_key)
        try:
            await warm.policy.edit_version_scheme(edit_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=str(project_key),
                tab="lifecycle",
                error=f"Error saving version scheme: {e}",
            )
        except ValueError as e:
            return await session.redirect(
                get.projects.view, project_key=str(project_key), tab="lifecycle", error=str(e)
            )

    return await session.redirect(
        get.projects.view, project_key=str(project_key), tab="lifecycle", success="Version scheme saved."
    )


async def _process_finish_form(
    session: web.Committer, finish_form: shared.projects.FinishPolicyForm
) -> web.WerkzeugResponse:
    project_key = finish_form.project_key

    async with storage.write(session) as write:
        warm = await write.as_project_release_manager(project_key)
        try:
            await warm.policy.edit_finish(finish_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=project_key,
                tab="finish",
                error=f"Error editing finish policy: {e}",
            )

    return await session.redirect(
        get.projects.view,
        project_key=project_key,
        tab="finish",
        success="Finish options saved successfully.",
    )


async def _process_remove_category(
    session: web.Committer, remove_form: shared.projects.RemoveCategoryForm
) -> web.WerkzeugResponse:
    project_key = remove_form.project_key
    category_to_remove = remove_form.category_to_remove

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        modified = await _metadata_category_remove(wacm, project_key, category_to_remove)

    if modified:
        return await session.redirect(
            get.projects.view,
            project_key=str(project_key),
            tab="metadata",
            success=f"Category '{category_to_remove}' removed.",
        )
    return await session.redirect(
        get.projects.view,
        project_key=str(project_key),
        tab="metadata",
        error=f"Category '{category_to_remove}' does not exist.",
    )


async def _process_remove_language(
    session: web.Committer, remove_form: shared.projects.RemoveLanguageForm
) -> web.WerkzeugResponse:
    project_key = remove_form.project_key
    language_to_remove = remove_form.language_to_remove

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        modified = await _metadata_language_remove(wacm, project_key, language_to_remove)

    if modified:
        return await session.redirect(
            get.projects.view,
            project_key=project_key,
            tab="metadata",
            success=f"Language '{language_to_remove}' removed.",
        )
    return await session.redirect(
        get.projects.view,
        project_key=project_key,
        tab="metadata",
        error=f"Language '{language_to_remove}' does not exist.",
    )


async def _process_security_form(
    session: web.Committer, security_form: shared.projects.SecurityForm
) -> web.WerkzeugResponse:
    project_key = security_form.project_key

    async with storage.write(session) as write:
        warm = await write.as_project_release_manager(project_key)
        try:
            await warm.project.edit_security(security_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=str(project_key),
                tab="security",
                error=f"Error saving security metadata: {e}",
            )
        except ValueError as e:
            return await session.redirect(get.projects.view, project_key=str(project_key), tab="security", error=str(e))

    return await session.redirect(
        get.projects.view, project_key=str(project_key), tab="security", success="Security metadata saved."
    )


async def _process_trusted_publishing_form(
    session: web.Committer, tp_form: shared.projects.TrustedPublishingPolicyForm
) -> web.WerkzeugResponse:
    project_key = tp_form.project_key

    async with storage.write(session) as write:
        warm = await write.as_project_release_manager(project_key)
        try:
            await warm.policy.edit_trusted_publishing(tp_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=project_key,
                tab="trusted-publishing",
                error=f"Error editing Trusted Publishing policy: {e}",
            )

    return await session.redirect(
        get.projects.view,
        project_key=project_key,
        tab="trusted-publishing",
        success="Trusted Publishing options saved successfully.",
    )


async def _process_vote_form(session: web.Committer, vote_form: shared.projects.VotePolicyForm) -> web.WerkzeugResponse:
    project_key = vote_form.project_key

    async with storage.write(session) as write:
        warm = await write.as_project_release_manager(project_key)
        try:
            await warm.policy.edit_vote(vote_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=project_key,
                tab="vote",
                error=f"Error editing vote policy: {e}",
            )

    return await session.redirect(
        get.projects.view,
        project_key=project_key,
        tab="vote",
        success="Vote options saved successfully.",
    )


async def _request_approval(
    session: web.Committer, project_key: safe.ProjectKey, action: sql.ApprovalAction
) -> web.WerkzeugResponse:
    if not config.get().CAP_ROLE_ACCOUNT_TOKEN:
        return await session.redirect(
            get.projects.projects, error="CAP approval is not configured. Please contact an administrator."
        )

    async with storage.write(session) as write:
        try:
            wacm = await write.as_project_committee_member(project_key)
        except storage.AccessError as e:
            return await session.redirect(get.projects.projects, error=f"Error requesting {action.value} approval: {e}")

        async with db.session() as data:
            project = await data.project(key=str(project_key), _committee=True, _releases=True).get()
            if (project is None) or (project.committee is None):
                return await session.redirect(get.projects.projects, error=f"Project '{project_key}' not found.")
            if project.committee.is_podling:
                return await session.redirect(
                    get.projects.projects,
                    error=(
                        "CAP approval voting is not available for podlings. Archival or deletion of a podling is an"
                        " Incubator PMC decision."
                    ),
                )
            committee_key = project.committee.key
            display_name = project.display_name
            active_siblings = len(
                await data.project(committee_key=committee_key, status=sql.ProjectStatus.ACTIVE, _committee=False).all()
            )
            eligibility_error = _action_eligibility_error(project, action, active_siblings)
            if eligibility_error is not None:
                return await session.redirect(get.projects.projects, error=eligibility_error)
            existing = await data.approval_request(
                project_key=str(project_key),
                status_in=[sql.ApprovalStatus.PENDING, sql.ApprovalStatus.APPROVED],
                release_version=None,
            ).get()
            if existing is not None:
                return await session.redirect(
                    get.projects.projects,
                    error=f"A CAP approval request to {existing.action.value} this project is already in progress.",
                )

        closes_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            minutes=constants.CAP_VOTE_DURATION_MINUTES
        )
        try:
            question = await util.cap_create_approval_question(
                action, project_key, display_name, committee_key, session.uid, closes_at
            )
        except util.FetchError as e:
            return await session.redirect(get.projects.projects, error=f"Could not create CAP approval vote: {e}")

        return await _persist_approval(
            session, wacm, project_key, committee_key, action, question.question_id, closes_at
        )


_VIEW_HANDLERS: Final[dict[type, Callable[[web.Committer, Any], Awaitable[web.WerkzeugResponse]]]] = {
    shared.projects.AddCategoryForm: _process_add_category,
    shared.projects.AddLanguageForm: _process_add_language,
    shared.projects.ComposePolicyForm: _process_compose_form,
    shared.projects.EditCycleDatesForm: _process_edit_cycle_dates_form,
    shared.projects.EditMetadataForm: _process_edit_metadata_form,
    shared.projects.EditVersionSchemeForm: _process_edit_version_scheme_form,
    shared.projects.FinishPolicyForm: _process_finish_form,
    shared.projects.SecurityForm: _process_security_form,
    shared.projects.TrustedPublishingPolicyForm: _process_trusted_publishing_form,
    shared.projects.RemoveCategoryForm: _process_remove_category,
    shared.projects.RemoveLanguageForm: _process_remove_language,
    shared.projects.VotePolicyForm: _process_vote_form,
}
