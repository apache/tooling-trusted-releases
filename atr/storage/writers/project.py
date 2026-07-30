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

# Removing this will cause circular imports
from __future__ import annotations

import datetime
import sqlite3
from typing import TYPE_CHECKING

import sqlalchemy.exc

import atr.catalog_site as catalog_site
import atr.constants as constants
import atr.cycles as cycles
import atr.db as db
import atr.models.api as api
import atr.models.calver as calver
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.registry as registry
import atr.storage as storage
import atr.tasks as tasks
import atr.util as util

if TYPE_CHECKING:
    import atr.shared as shared


class GeneralPublic:
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsGeneralPublic,
        data: db.Session,
    ):
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        self.__asf_uid = write.authorisation.asf_uid


class FoundationCommitter(GeneralPublic):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationCommitter, data: db.Session):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid


class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key


class ReleaseManager(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsReleaseManager,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data, committee_key)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    async def edit_metadata(self, form: shared.projects.EditMetadataForm) -> None:
        project = await self.__validate_project_in_committee(form.project_key)

        project.name = form.display_name
        project.description = form.description.strip() or None
        project.short_description = form.short_description.strip() or None
        project.homepage = str(form.homepage) if form.homepage else None
        project.lifecycle_page = str(form.lifecycle_page) if form.lifecycle_page else None
        project.download_page = str(form.download_page) if form.download_page else None
        project.bug_database = str(form.bug_database) if form.bug_database else None
        project.mailing_lists = str(form.mailing_lists) if form.mailing_lists else None
        project.repositories = list(form.repositories)
        project.standards = list(form.standards)
        project.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)

        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project.key),
        )

    async def edit_security(self, form: shared.projects.SecurityForm) -> None:
        project = await self.__validate_project_in_committee(form.project_key)
        contact = form.security_contact.strip() or None
        validation.validate_security_contact(self.__committee_key, contact)
        project.security_contact = contact
        project.threat_model_link = str(form.threat_model_link) if form.threat_model_link else None
        project.threat_model_src_link = str(form.threat_model_src_link) if form.threat_model_src_link else None
        project.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)

        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project.key),
        )

    async def set_download_page(self, project_key: safe.ProjectKey, url: str) -> str | None:
        if error := util.download_page_url_error(url):
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project_key),
                download_page=url,
                rejected=error,
            )
            raise storage.AccessError(error, status=400)
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            project = await self.__validate_project_in_committee(project_key)
            if existing := project.download_page:
                await self.__data.rollback()
                return existing
            project.download_page = url
            project.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            download_page=url,
        )
        return None

    async def __validate_project_in_committee(self, project_key: safe.ProjectKey) -> sql.Project:
        project = await self.__data.project(key=str(project_key), _committee=True).demand(
            storage.AccessError(f"Project '{project_key}' not found.", status=404)
        )
        if project.committee_key != self.__committee_key:
            raise storage.AccessError(f"Project {project_key} is not in committee {self.__committee_key}", status=403)
        if project.status == sql.ProjectStatus.RETIRED:
            raise storage.AccessError(f"Project '{project.key}' is retired; metadata edits are disabled.")
        return project


class CommitteeMember(ReleaseManager):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data, committee_key)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    async def category_add(self, project_key: safe.ProjectKey, new_category: str) -> bool:
        project = await self.__validate_project_in_committee(project_key)
        new_category = new_category.strip()
        current_categories = self.__current_categories(project)
        if new_category and (new_category not in current_categories):
            if ":" in new_category:
                raise ValueError(f"Category '{new_category}' contains a colon")
            if new_category in registry.FORBIDDEN_PROJECT_CATEGORIES:
                raise ValueError(f"Category '{new_category}' may not be added or removed")
            current_categories.append(new_category)
            current_categories.sort()
            project.categories = ", ".join(current_categories)
            if project.categories == "":
                project.categories = None
            project.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project.key),
                category=new_category,
            )
            return True
        return False

    async def category_remove(self, project_key: safe.ProjectKey, action_value: str) -> bool:
        project = await self.__validate_project_in_committee(project_key)
        current_categories = self.__current_categories(project)
        if action_value in current_categories:
            if action_value in registry.FORBIDDEN_PROJECT_CATEGORIES:
                raise ValueError(f"Category '{action_value}' may not be added or removed")
            current_categories.remove(action_value)
            project.categories = ", ".join(current_categories)
            if project.categories == "":
                project.categories = None
            project.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project.key),
                category=action_value,
            )
            return True
        return False

    async def create(self, display_name: str, project_key: safe.ProjectKey) -> None:
        committee_key = safe.CommitteeKey(self.__committee_key)
        try:
            await _build_and_add_project(self.__data, self.__asf_uid, committee_key, display_name, project_key)
            await self.__data.commit()
        except sqlalchemy.exc.IntegrityError as e:
            if (
                isinstance(e.orig, sqlite3.IntegrityError)
                and (e.orig.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY)
                and ("project.key" in str(e.orig))
            ):
                raise storage.AccessError(f"Project {project_key} already exists", status=409)
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=str(committee_key),
            project_key=str(project_key),
        )

    async def archive(self, project_key: safe.ProjectKey, approval_request_id: int) -> None:
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            await self.__claim_approval(approval_request_id, project_key, sql.ApprovalAction.ARCHIVE)
            project = await self.__data.project(
                key=str(project_key), status=sql.ProjectStatus.ACTIVE, _releases=True
            ).get()
            if not project:
                raise storage.AccessError(f"Project '{project_key}' not found.")
            if project.committee_key != self.__committee_key:
                raise storage.AccessError(
                    f"Project {project_key} is not in committee {self.__committee_key}", status=403
                )

            # TODO: when a project has current or archived releases, archiving the project
            # should be allowed and should cascade-archive the current releases as well -
            # for now we just block, and only drafts are handled (deleted by the caller)
            post_draft_phases = {
                sql.ReleasePhase.RELEASE_CANDIDATE,
                sql.ReleasePhase.RELEASE_PREVIEW,
                sql.ReleasePhase.RELEASE,
            }
            if any(r.phase in post_draft_phases for r in project.releases_including_embargoed):
                raise storage.AccessError(
                    f"Cannot archive project '{project_key}' because it has active releases;"
                    " complete or remove them first."
                )

            if project.releases_including_embargoed:
                raise storage.AccessError(
                    f"Cannot archive project '{project_key}' because it still has draft releases;"
                    " delete them and try again."
                )

            if project.committee_key:
                committee_projects = await self.__data.project(
                    committee_key=project.committee_key, status=sql.ProjectStatus.ACTIVE
                ).all()
                if len(committee_projects) <= 1:
                    raise storage.AccessError(
                        f"Cannot archive project '{project_key}' because it is the only project in its committee."
                    )

            project.status = sql.ProjectStatus.RETIRED
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            approval_request_id=approval_request_id,
        )

    async def delete(self, project_key: safe.ProjectKey, approval_request_id: int) -> None:
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            await self.__claim_approval(approval_request_id, project_key, sql.ApprovalAction.DELETE)
            project = await self.__data.project(
                key=str(project_key), status=sql.ProjectStatus.ACTIVE, _releases=True
            ).get()
            if not project:
                raise storage.AccessError(f"Project '{project_key}' not found.", status=404)
            if project.committee_key != self.__committee_key:
                raise storage.AccessError(
                    f"Project {project_key} is not in committee {self.__committee_key}", status=403
                )

            if project.committee_key:
                committee_projects = await self.__data.project(
                    committee_key=project.committee_key, status=sql.ProjectStatus.ACTIVE
                ).all()
                if len(committee_projects) <= 1:
                    raise storage.AccessError(
                        f"Cannot delete project '{project_key}' because it is the only project in its committee."
                    )

            if project.releases_including_embargoed:
                raise storage.AccessError(
                    f"Cannot delete project '{project_key}' because it has associated releases. Delete them first.",
                    status=409,
                )

            await self.__data.delete(project)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            approval_request_id=approval_request_id,
        )
        return None

    async def language_add(self, project_key: safe.ProjectKey, new_language: str) -> bool:
        project = await self.__validate_project_in_committee(project_key)
        new_language = new_language.strip()
        current_languages = self.__current_languages(project)
        if new_language and (new_language not in current_languages):
            if ":" in new_language:
                raise ValueError(f"Language '{new_language}' contains a colon")
            current_languages.append(new_language)
            current_languages.sort()
            project.programming_languages = ", ".join(current_languages)
            if project.programming_languages == "":
                project.programming_languages = None
            project.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project.key),
                language=new_language,
            )
            return True
        return False

    async def language_remove(self, project_key: safe.ProjectKey, action_value: str) -> bool:
        project = await self.__validate_project_in_committee(project_key)
        current_languages = self.__current_languages(project)
        if action_value in current_languages:
            current_languages.remove(action_value)
            project.programming_languages = ", ".join(current_languages)
            if project.programming_languages == "":
                project.programming_languages = None
            project.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project.key),
                language=action_value,
            )
            return True
        return False

    async def request_approval(
        self,
        project_key: safe.ProjectKey,
        action: sql.ApprovalAction,
        cap_question_id: int,
        closes_at: datetime.datetime,
        release_version: safe.VersionKey | None = None,
    ) -> sql.ApprovalRequest:
        await self.__validate_project_in_committee(project_key)
        approval = sql.ApprovalRequest(
            project_key=str(project_key),
            committee_key=self.__committee_key,
            action=action,
            release_version=str(release_version) if release_version else None,
            cap_question_id=cap_question_id,
            requested_by=self.__asf_uid,
            closes_at=closes_at,
        )
        try:
            self.__data.add(approval)
            await self.__data.flush()
            await tasks.cap_approval_resolve(
                util.unwrap(approval.id),
                schedule=closes_at + constants.CAP_RESOLVE_INITIAL_DELAY,
                caller_data=self.__data,
            )
            await self.__data.commit()
        except sqlalchemy.exc.IntegrityError as error:
            await self.__data.rollback()
            if "approvalrequest.release_version" in str(error):
                raise storage.AccessError("A CAP approval request for this release is already in progress.", status=409)
            if "approvalrequest.project_key" in str(error):
                raise storage.AccessError("A CAP approval request for this project is already in progress.", status=409)
            raise storage.AccessError(
                f"CAP issued question #{cap_question_id}, which ATR has already recorded for another request.",
                status=409,
            )
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            approval_action=action.value,
            cap_question_id=cap_question_id,
            closes_at=closes_at.isoformat(),
        )
        return approval

    async def __claim_approval(
        self, approval_request_id: int, project_key: safe.ProjectKey, action: sql.ApprovalAction
    ) -> None:
        approval = await self.__data.approval_request(id=approval_request_id).get()
        if (approval is None) or (approval.status != sql.ApprovalStatus.APPROVED):
            raise storage.AccessError("This approval request is not ready to complete.", status=409)
        if (approval.project_key != str(project_key)) or (approval.action != action):
            raise storage.AccessError("This approval request does not match the requested action.", status=409)
        if approval.committee_key != self.__committee_key:
            raise storage.AccessError("This approval request was filed for a different committee.", status=409)
        approval.status = sql.ApprovalStatus.COMPLETED

    def __current_categories(self, project: sql.Project) -> list[str]:
        return (
            [category.strip() for category in (project.categories or "").split(",") if category.strip()]
            if project.categories
            else []
        )

    def __current_languages(self, project: sql.Project) -> list[str]:
        return (
            [language.strip() for language in (project.programming_languages or "").split(",") if language.strip()]
            if project.programming_languages
            else []
        )

    async def __validate_project_in_committee(self, project_key: safe.ProjectKey) -> sql.Project:
        project = await self.__data.project(key=str(project_key)).demand(
            storage.AccessError(f"Project '{project_key}' not found.", status=404)
        )
        if project.committee_key != self.__committee_key:
            raise storage.AccessError(f"Project {project_key} is not in committee {self.__committee_key}", status=403)
        storage.ensure_project_active(project)
        return project


# No committee gate - the caller's right to act for the committee must be
# established upstream, in the .asf.yaml feature.
class FoundationAdmin(FoundationCommitter):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationAdmin, data: db.Session):
        super().__init__(write, write_as, data)
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid

    async def record_approval_failure(self, approval_request_id: int, error: str) -> bool:
        """Mark an approved request FAILED because its completion could not be carried out.

        The vote itself passed, so `outcome` is left as it was - `error` carries
        why the follow-on action (e.g. auto-archival) could not complete.
        """
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            approval = await self.__data.approval_request(id=approval_request_id).get()
            if (approval is None) or (approval.status != sql.ApprovalStatus.APPROVED):
                await self.__data.rollback()
                return False
            approval.status = sql.ApprovalStatus.FAILED
            approval.error = error
            approval.resolved_at = datetime.datetime.now(datetime.UTC)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            approval_request_id=approval_request_id,
            approval_status=sql.ApprovalStatus.FAILED.value,
            error=error,
        )
        return True

    async def record_approval_outcome(
        self,
        approval_request_id: int,
        status: sql.ApprovalStatus,
        vote_outcome: str | None,
        permalink: str | None,
        error: str | None = None,
    ) -> bool:
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            approval = await self.__data.approval_request(id=approval_request_id).get()
            if (approval is None) or (approval.status != sql.ApprovalStatus.PENDING):
                await self.__data.rollback()
                return False
            approval.status = status
            approval.outcome = vote_outcome
            approval.permalink = permalink
            approval.error = error
            if error is None:
                approval.resolved_at = datetime.datetime.now(datetime.UTC)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            approval_request_id=approval_request_id,
            approval_status=status.value,
            outcome=vote_outcome,
            error=error,
        )
        return True

    async def upsert_config(
        self,
        args: api.ProjectConfigArgs,
        update_type: sql.UpdateType,
    ) -> bool:
        try:
            await self.__data.begin_immediate()
            project, created = await self.__resolve_or_create_project_no_commit(args)
            project_args = args.project
            if (project_args is not None) and project_args.model_fields_set:
                await self.__apply_project_args_no_commit(project, project_args)
            policy_args = args.policy
            if (policy_args is not None) and policy_args.model_fields_set:
                policy_update = api.PolicyUpdateArgs(
                    project=args.project_key,
                    **policy_args.model_dump(exclude_unset=True),
                )
                await self.__write_as.policy.edit_no_commit(args.project_key, policy_update)
            project.mark_updated(by=self.__asf_uid, update_type=update_type)
            await self.__data.commit()
        except sqlalchemy.exc.IntegrityError as e:
            await self.__data.rollback()
            if (
                isinstance(e.orig, sqlite3.IntegrityError)
                and (e.orig.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY)
                and ("project.key" in str(e.orig))
            ):
                raise storage.AccessError(f"Project {args.project_key} already exists", status=409)
            raise
        except Exception:
            await self.__data.rollback()
            raise

        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=str(args.committee_key),
            project_key=str(args.project_key),
            created=created,
        )
        return created

    async def __resolve_or_create_project_no_commit(
        self,
        args: api.ProjectConfigArgs,
    ) -> tuple[sql.Project, bool]:
        existing = await self.__data.project(key=str(args.project_key)).get()
        if (existing is not None) and (existing.committee_key != str(args.committee_key)):
            raise storage.AccessError(
                f"Project '{args.project_key}' does not belong to committee '{args.committee_key}'",
                status=400,
            )
        if existing is not None:
            storage.ensure_project_active(existing)
            return existing, False
        if (args.project is None) or (args.project.name is None) or (not args.project.name.strip()):
            raise ValueError(f"Project '{args.project_key}' does not exist; project.name is required to create it")
        project = await _build_and_add_project(
            self.__data,
            self.__asf_uid,
            args.committee_key,
            display_name=args.project.name,
            project_key=args.project_key,
        )
        return project, True

    async def __apply_project_args_no_commit(
        self,
        project: sql.Project,
        args: api.ProjectConfigProjectArgs,
    ) -> None:
        str_fields = {
            "name",
            "description",
            "short_description",
            "homepage",
            "lifecycle_page",
            "download_page",
            "bug_database",
            "mailing_lists",
            "security_contact",
            "threat_model_link",
            "threat_model_src_link",
            "version_pattern",
            "cycle_match",
            "branch_template",
        }
        list_fields = {"repositories", "standards"}
        csv_fields = {"categories", "programming_languages"}
        version_scheme_fields = {
            "version_method",
            "version_pattern",
            "cycle_match",
            "calver_format",
            "branch_template",
        }
        provided = args.model_fields_set

        for field in str_fields & provided:
            value = getattr(args, field)
            if value is not None:
                value = str(value).strip() or None
            setattr(project, field, value)
        for field in list_fields & provided:
            setattr(project, field, [str(item) for item in getattr(args, field) or []])
        for field in csv_fields & provided:
            items = [str(item).strip() for item in getattr(args, field) or []]
            joined = ", ".join(item for item in items if item)
            setattr(project, field, joined or None)
        if version_scheme_fields & provided:
            _apply_version_scheme(project, args, provided)
            await cycles.reassign_release_cycles(self.__data, project)
            await catalog_site.queue_regeneration(self.__data, self.__asf_uid, project.key)


def _apply_version_scheme(project: sql.Project, args: api.ProjectConfigProjectArgs, provided: set[str]) -> None:
    if "version_method" in provided:
        project.version_method = args.version_method or sql.VersionMethod.SIMPLE

    if project.version_method is not sql.VersionMethod.CALVER:
        if "calver_format" in provided:
            raise ValueError("Field 'calver_format' applies only when version_method is 'calver'")
        if "version_method" in provided:
            # A project that has moved off calver keeps no date format behind
            project.calver_format = None
        return

    # Cycle membership for a calver project comes from its date format, so
    # cycle_match is compiled here rather than authored
    if "cycle_match" in provided:
        raise ValueError("Calver projects derive 'cycle_match' from 'calver_format'; set 'calver_format' instead")
    if "calver_format" in provided:
        project.calver_format = (args.calver_format or "").strip() or None
    if {"calver_format", "version_method"} & provided:
        project.cycle_match = calver.cycle_regex(project.calver_format) if project.calver_format else None


async def _build_and_add_project(
    data: db.Session,
    asf_uid: str,
    committee_key: safe.CommitteeKey,
    display_name: str,
    project_key: safe.ProjectKey,
) -> sql.Project:
    super_project = None
    key = str(project_key)
    # Get the base project to derive from
    # We're allowing derivation from a retired project here
    # TODO: Should we disallow this instead?
    committee_projects = await data.project(
        committee_key=str(committee_key), _committee=True, _release_policy=True
    ).all()
    for committee_project in committee_projects:
        if key.startswith(str(committee_project.key) + "-"):
            if (super_project is None) or (len(str(super_project.key)) < len(str(committee_project.key))):
                super_project = committee_project

    # Check whether the project already exists
    if await data.project(key=key).get():
        raise storage.AccessError(f"Project {key} already exists", status=409)

    now = datetime.datetime.now(datetime.UTC)
    project = sql.Project(
        key=key,
        name=display_name,
        status=sql.ProjectStatus.ACTIVE,
        super_project_key=super_project.key if super_project else None,
        description=super_project.description if super_project else None,
        categories=super_project.categories if super_project else None,
        programming_languages=super_project.programming_languages if super_project else None,
        version_method=super_project.version_method if super_project else sql.VersionMethod.SIMPLE,
        version_pattern=super_project.version_pattern if super_project else None,
        cycle_match=super_project.cycle_match if super_project else None,
        calver_format=super_project.calver_format if super_project else None,
        branch_template=super_project.branch_template if super_project else None,
        committee_key=str(committee_key),
        created=now,
        created_by=asf_uid,
        updated=now,
        updated_by=asf_uid,
    )
    if super_project and super_project.release_policy:
        project.release_policy = super_project.release_policy.duplicate()
    data.add(project)
    return project
