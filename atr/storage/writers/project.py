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

import atr.cycles as cycles
import atr.db as db
import atr.models.api as api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.registry as registry
import atr.storage as storage

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


class CommitteeMember(CommitteeParticipant):
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
        project = await self.__data.project(key=str(project_key)).get()
        if not project:
            raise storage.AccessError(f"Project '{project_key}' not found.", status=404)
        new_category = new_category.strip()
        current_categories = self.__current_categories(project)
        if new_category and (new_category not in current_categories):
            if ":" in new_category:
                raise ValueError(f"Category '{new_category}' contains a colon")
            if new_category in registry.FORBIDDEN_PROJECT_CATEGORIES:
                raise ValueError(f"Category '{new_category}' may not be added or removed")
            current_categories.append(new_category)
            current_categories.sort()
            project.category = ", ".join(current_categories)
            if project.category == "":
                project.category = None
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project.key),
                category=new_category,
            )
            return True
        return False

    async def category_remove(self, project_key: safe.ProjectKey, action_value: str) -> bool:
        project = await self.__data.project(key=str(project_key)).get()
        if not project:
            raise storage.AccessError(f"Project '{project_key}' not found.", status=404)
        current_categories = self.__current_categories(project)
        if action_value in current_categories:
            if action_value in registry.FORBIDDEN_PROJECT_CATEGORIES:
                raise ValueError(f"Category '{action_value}' may not be added or removed")
            current_categories.remove(action_value)
            project.category = ", ".join(current_categories)
            if project.category == "":
                project.category = None
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project.key),
                category=action_value,
            )
            return True
        return False

    async def create(self, committee_key: safe.CommitteeKey, display_name: str, label: str) -> None:
        try:
            await self._build_and_add_project_no_commit(committee_key, display_name, label)
            await self.__data.commit()
        except sqlalchemy.exc.IntegrityError as e:
            if (
                isinstance(e.orig, sqlite3.IntegrityError)
                and (e.orig.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY)
                and ("project.key" in str(e.orig))
            ):
                raise storage.AccessError(f"Project {label} already exists", status=409)
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=str(committee_key),
            project_key=label,
        )

    async def _build_and_add_project_no_commit(
        self,
        committee_key: safe.CommitteeKey,
        display_name: str,
        label: str,
    ) -> sql.Project:
        super_project = None
        # TODO: Do we need to do any additional validation on the string value?
        # Get the base project to derive from
        # We're allowing derivation from a retired project here
        # TODO: Should we disallow this instead?
        committee_projects = await self.__data.project(
            committee_key=str(committee_key), _committee=True, _release_policy=True
        ).all()
        for committee_project in committee_projects:
            if label.startswith(str(committee_project.key) + "-"):
                if (super_project is None) or (len(str(super_project.key)) < len(str(committee_project.key))):
                    super_project = committee_project

        # Check whether the project already exists
        if await self.__data.project(key=label).get():
            raise storage.AccessError(f"Project {label} already exists", status=409)

        project = sql.Project(
            key=label,
            name=display_name,
            status=sql.ProjectStatus.ACTIVE,
            super_project_key=super_project.key if super_project else None,
            description=super_project.description if super_project else None,
            category=super_project.category if super_project else None,
            programming_languages=super_project.programming_languages if super_project else None,
            committee_key=str(committee_key),
            created=datetime.datetime.now(datetime.UTC),
            created_by=self.__asf_uid,
        )
        if super_project and super_project.release_policy:
            project.release_policy = super_project.release_policy.duplicate()
        self.__data.add(project)
        return project

    async def delete(self, project_key: safe.ProjectKey) -> None:
        project = await self.__data.project(
            key=str(project_key), status=sql.ProjectStatus.ACTIVE, _releases=True, _distribution_channels=True
        ).get()

        if not project:
            raise storage.AccessError(f"Project '{project_key}' not found.", status=404)

        # Prevent deletion if there are associated releases or channels
        if project.releases:
            raise storage.AccessError(
                f"Cannot delete project '{project_key}' because it has associated releases.", status=409
            )

        await self.__data.delete(project)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
        )
        return None

    async def edit_metadata(self, form: shared.projects.EditMetadataForm) -> None:
        project = await self.__data.project(key=str(form.project_key)).get()
        if not project:
            raise storage.AccessError(f"Project '{form.project_key}' not found.", status=404)

        project.homepage = str(form.homepage) if form.homepage else None
        project.lifecycle_page = str(form.lifecycle_page) if form.lifecycle_page else None
        project.download_page = str(form.download_page) if form.download_page else None
        project.bug_database = str(form.bug_database) if form.bug_database else None
        project.mailing_lists = str(form.mailing_lists) if form.mailing_lists else None
        project.repository = list(form.repository)
        project.standards = list(form.standards)

        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project.key),
        )

    async def language_add(self, project_key: safe.ProjectKey, new_language: str) -> bool:
        project = await self.__data.project(key=str(project_key)).get()
        if not project:
            raise storage.AccessError(f"Project '{project_key}' not found.", status=404)
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
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project.key),
                language=new_language,
            )
            return True
        return False

    async def language_remove(self, project_key: safe.ProjectKey, action_value: str) -> bool:
        project = await self.__data.project(key=str(project_key)).get()
        if not project:
            raise storage.AccessError(f"Project '{project_key}' not found.", status=404)
        current_languages = self.__current_languages(project)
        if action_value in current_languages:
            current_languages.remove(action_value)
            project.programming_languages = ", ".join(current_languages)
            if project.programming_languages == "":
                project.programming_languages = None
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project.key),
                language=action_value,
            )
            return True
        return False

    async def upsert_config(
        self,
        args: api.ProjectConfigArgs,
    ) -> bool:
        try:
            await self.__data.begin_immediate()
            project, created = await self._resolve_or_create_project_no_commit(args)
            project_args = args.project
            if (project_args is not None) and project_args.model_fields_set:
                await self._apply_project_args_no_commit(project, project_args)
            policy_args = args.policy
            if (policy_args is not None) and policy_args.model_fields_set:
                policy_update = api.PolicyUpdateArgs(
                    project=args.project_key,
                    **policy_args.model_dump(exclude_unset=True),
                )
                await self.__write_as.policy._edit_policy_no_commit(args.project_key, policy_update)
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

    async def _resolve_or_create_project_no_commit(
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
            return existing, False
        if (args.project is None) or (args.project.name is None) or (not args.project.name.strip()):
            raise ValueError(f"Project '{args.project_key}' does not exist; project.name is required to create it")
        project = await self._build_and_add_project_no_commit(
            args.committee_key, display_name=args.project.name, label=str(args.project_key)
        )
        return project, True

    async def _apply_project_args_no_commit(
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
            "version_pattern",
            "cycle_match",
            "branch_template",
        }
        list_fields = {"repository", "standards"}
        version_scheme_fields = {"version_method", "version_pattern", "cycle_match", "branch_template"}
        provided = args.model_fields_set

        for field in str_fields & provided:
            value = getattr(args, field)
            if value is not None:
                value = str(value).strip() or None
            setattr(project, field, value)
        for field in list_fields & provided:
            setattr(project, field, [str(item) for item in getattr(args, field) or []])
        if "version_method" in provided:
            project.version_method = args.version_method or sql.VersionMethod.SIMPLE

        if version_scheme_fields & provided:
            await cycles.reassign_release_cycles(self.__data, project)

    def __current_categories(self, project: sql.Project) -> list[str]:
        return (
            [category.strip() for category in (project.category or "").split(",") if category.strip()]
            if project.category
            else []
        )

    def __current_languages(self, project: sql.Project) -> list[str]:
        return (
            [language.strip() for language in (project.programming_languages or "").split(",") if language.strip()]
            if project.programming_languages
            else []
        )
