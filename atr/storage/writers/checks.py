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

import sqlmodel

import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.storage as storage


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
            raise storage.AccessError("Not authorized")
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
            raise storage.AccessError("Not authorized")
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
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name

    async def ignore_add(
        self,
        project_name: safe.ProjectKey,
        release_glob: str | None = None,
        revision_number: safe.RevisionNumber | None = None,
        checker_glob: str | None = None,
        primary_rel_path_glob: str | None = None,
        member_rel_path_glob: str | None = None,
        status: sql.CheckResultStatusIgnore | None = None,
        message_glob: str | None = None,
    ) -> None:
        await self.__validate_project_in_committee(str(project_name))
        _validate_ignore_patterns(
            release_glob,
            checker_glob,
            primary_rel_path_glob,
            member_rel_path_glob,
            message_glob,
        )
        cri = sql.CheckResultIgnore(
            asf_uid=self.__asf_uid,
            created=datetime.datetime.now(datetime.UTC),
            project_name=str(project_name),
            release_glob=release_glob,
            revision_number=str(revision_number),
            checker_glob=checker_glob,
            primary_rel_path_glob=primary_rel_path_glob,
            member_rel_path_glob=member_rel_path_glob,
            status=status,
            message_glob=message_glob,
        )
        self.__data.add(cri)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            cri=cri.model_dump_json(exclude_none=True),
        )

    async def ignore_delete(self, id: int) -> None:
        cri = await self.__data.get(sql.CheckResultIgnore, id)
        if cri is None:
            raise storage.AccessError(f"Ignore {id} not found")
        await self.__validate_project_in_committee(cri.project_name)
        via = sql.validate_instrumented_attribute
        await self.__data.execute(sqlmodel.delete(sql.CheckResultIgnore).where(via(sql.CheckResultIgnore.id) == id))
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            ignore_id=id,
        )

    async def ignore_update(
        self,
        id: int,
        release_glob: str | None = None,
        revision_number: str | None = None,
        checker_glob: str | None = None,
        primary_rel_path_glob: str | None = None,
        member_rel_path_glob: str | None = None,
        status: sql.CheckResultStatusIgnore | None = None,
        message_glob: str | None = None,
    ) -> None:
        cri = await self.__data.get(sql.CheckResultIgnore, id)
        if cri is None:
            raise storage.AccessError(f"Ignore {id} not found")
        await self.__validate_project_in_committee(cri.project_name)
        _validate_ignore_patterns(
            release_glob,
            checker_glob,
            primary_rel_path_glob,
            member_rel_path_glob,
            message_glob,
        )
        # The updating ASF UID is now responsible for the whole ignore
        cri.asf_uid = self.__asf_uid
        cri.release_glob = release_glob
        cri.revision_number = revision_number
        cri.checker_glob = checker_glob
        cri.primary_rel_path_glob = primary_rel_path_glob
        cri.member_rel_path_glob = member_rel_path_glob
        cri.status = status
        cri.message_glob = message_glob
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            cri=cri.model_dump_json(exclude_none=True),
        )

    async def __validate_project_in_committee(self, project_name: str) -> None:
        project = await self.__data.project(project_name, _committee=True).get()
        if project is None:
            raise storage.AccessError(f"Project {project_name} not found")
        if project.committee_name != self.__committee_name:
            raise storage.AccessError(f"Project {project_name} is not in committee {self.__committee_name}")


def _validate_ignore_patterns(*patterns: str | None) -> None:
    for pattern in patterns:
        if pattern is None:
            continue
        validation.validate_ignore_pattern(pattern)
