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

from typing import TYPE_CHECKING

import atr.db as db
import atr.models as models
import atr.storage as storage
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
            raise storage.AccessError("No ASF UID")
        self.__asf_uid = asf_uid


class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_name: str,
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("No ASF UID")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name


class CommitteeMember(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_name: str,
    ):
        super().__init__(write, write_as, data, committee_name)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("No ASF UID")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name

    async def edit_compose(self, form: shared.projects.ComposePolicyForm) -> None:
        project_name = form.project_name
        _, release_policy = await self.__get_or_create_policy(project_name)

        release_policy.source_artifact_paths = _split_lines(form.source_artifact_paths)
        release_policy.license_check_mode = form.license_check_mode  # pyright: ignore[reportAttributeAccessIssue]
        release_policy.source_excludes_lightweight = _split_lines_verbatim(form.source_excludes_lightweight)
        release_policy.source_excludes_rat = _split_lines_verbatim(form.source_excludes_rat)
        release_policy.binary_artifact_paths = _split_lines(form.binary_artifact_paths)
        release_policy.github_repository_name = form.github_repository_name.strip()
        release_policy.github_compose_workflow_path = _split_lines(form.github_compose_workflow_path)
        release_policy.strict_checking = form.strict_checking

        await self.__commit_and_log(project_name)

    async def edit_finish(self, form: shared.projects.FinishPolicyForm) -> None:
        project_name = form.project_name
        project, release_policy = await self.__get_or_create_policy(project_name)

        release_policy.github_finish_workflow_path = _split_lines(form.github_finish_workflow_path)
        self.__set_announce_release_subject(form.announce_release_subject or "", project, release_policy)
        self.__set_announce_release_template(form.announce_release_template or "", project, release_policy)
        release_policy.preserve_download_files = form.preserve_download_files

        await self.__commit_and_log(project_name)

    async def edit_vote(self, form: shared.projects.VotePolicyForm) -> None:
        project_name = form.project_name
        project, release_policy = await self.__get_or_create_policy(project_name)

        release_policy.manual_vote = form.manual_vote

        if not release_policy.manual_vote:
            release_policy.github_vote_workflow_path = _split_lines(form.github_vote_workflow_path)
            release_policy.mailto_addresses = [form.mailto_addresses]
            self.__set_min_hours(form.min_hours, project, release_policy)
            release_policy.pause_for_rm = form.pause_for_rm
            release_policy.release_checklist = form.release_checklist or ""
            release_policy.vote_comment_template = form.vote_comment_template or ""
            self.__set_start_vote_subject(form.start_vote_subject or "", project, release_policy)
            self.__set_start_vote_template(form.start_vote_template or "", project, release_policy)
        elif project.committee and project.committee.is_podling:
            raise storage.AccessError("Manual voting is not allowed for podlings.")

        await self.__commit_and_log(project_name)

    async def __commit_and_log(self, project_name: str) -> None:
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_name=project_name,
        )

    async def __get_or_create_policy(self, project_name: str) -> tuple[models.sql.Project, models.sql.ReleasePolicy]:
        project = await self.__data.project(
            name=project_name, status=models.sql.ProjectStatus.ACTIVE, _release_policy=True, _committee=True
        ).demand(storage.AccessError(f"Project {project_name} not found"))

        release_policy = project.release_policy
        if release_policy is None:
            release_policy = models.sql.ReleasePolicy(project=project)
            project.release_policy = release_policy
            self.__data.add(release_policy)

        return project, release_policy

    def __set_announce_release_subject(
        self,
        submitted_subject: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        submitted_subject = submitted_subject.strip()
        current_default_text = project.policy_announce_release_subject_default
        current_default_hash = util.compute_sha3_256(current_default_text.encode())
        submitted_hash = util.compute_sha3_256(submitted_subject.encode())

        if submitted_hash == current_default_hash:
            release_policy.announce_release_subject = ""
        else:
            release_policy.announce_release_subject = submitted_subject

    def __set_announce_release_template(
        self,
        submitted_template: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        submitted_template = submitted_template.replace("\r\n", "\n")
        current_default_text = project.policy_announce_release_default
        current_default_hash = util.compute_sha3_256(current_default_text.encode())
        submitted_hash = util.compute_sha3_256(submitted_template.encode())

        if submitted_hash == current_default_hash:
            release_policy.announce_release_template = ""
        else:
            release_policy.announce_release_template = submitted_template

    def __set_min_hours(
        self,
        submitted_min_hours: int,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        current_system_default = project.policy_default_min_hours

        if submitted_min_hours == current_system_default:
            release_policy.min_hours = None
        else:
            release_policy.min_hours = submitted_min_hours

    def __set_start_vote_subject(
        self,
        submitted_subject: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        submitted_subject = submitted_subject.strip()
        current_default_text = project.policy_start_vote_subject_default
        current_default_hash = util.compute_sha3_256(current_default_text.encode())
        submitted_hash = util.compute_sha3_256(submitted_subject.encode())

        if submitted_hash == current_default_hash:
            release_policy.start_vote_subject = ""
        else:
            release_policy.start_vote_subject = submitted_subject

    def __set_start_vote_template(
        self,
        submitted_template: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        submitted_template = submitted_template.replace("\r\n", "\n")
        current_default_text = project.policy_start_vote_default
        current_default_hash = util.compute_sha3_256(current_default_text.encode())
        submitted_hash = util.compute_sha3_256(submitted_template.encode())

        if submitted_hash == current_default_hash:
            release_policy.start_vote_template = ""
        else:
            release_policy.start_vote_template = submitted_template


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


def _split_lines_verbatim(text: str) -> list[str]:
    # This still excludes empty lines
    return [line for line in text.split("\n") if line]
