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

import asyncio
import datetime
from typing import TYPE_CHECKING

import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.tasks.sbom as sbom
import atr.util as util

if TYPE_CHECKING:
    import pathlib


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
        committee_key: str,
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    async def augment_cyclonedx(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        revision_number: safe.RevisionNumber,
        rel_path: safe.RelPath,
    ) -> sql.Task:
        sbom_task = sql.Task(
            task_type=sql.TaskType.SBOM_AUGMENT,
            task_args=sbom.FileArgs(
                project_key=project_key,
                version_key=version_key,
                revision_number=revision_number,
                file_path=rel_path,
                asf_uid=util.unwrap(self.__asf_uid),
            ).model_dump(),
            asf_uid=util.unwrap(self.__asf_uid),
            added=datetime.datetime.now(datetime.UTC),
            status=sql.TaskStatus.QUEUED,
            project_key=str(project_key),
            version_key=str(version_key),
            revision_number=str(revision_number),
            primary_rel_path=str(rel_path),
        )
        self.__data.add(sbom_task)
        await self.__data.commit()
        await self.__data.refresh(sbom_task)
        return sbom_task

    async def generate_cyclonedx(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        revision_number: str,
        path_in_new_revision: pathlib.Path,
        sbom_path_in_new_revision: pathlib.Path,
    ) -> sql.Task:
        # Create and queue the task, using paths within the new revision
        # We still need release.name for the task metadata
        artifact_path = await asyncio.to_thread(_resolved_path_str, path_in_new_revision)
        output_path = await asyncio.to_thread(_resolved_path_str, sbom_path_in_new_revision)
        sbom_task = sql.Task(
            task_type=sql.TaskType.SBOM_GENERATE_CYCLONEDX,
            task_args=sbom.GenerateCycloneDX(
                artifact_path=artifact_path,
                output_path=output_path,
            ).model_dump(),
            asf_uid=util.unwrap(self.__asf_uid),
            added=datetime.datetime.now(datetime.UTC),
            status=sql.TaskStatus.QUEUED,
            project_key=str(project_key),
            version_key=str(version_key),
            revision_number=revision_number,
        )
        self.__data.add(sbom_task)
        await self.__data.commit()
        await self.__data.refresh(sbom_task)
        return sbom_task

    async def osv_scan_cyclonedx(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        revision_number: safe.RevisionNumber,
        rel_path: safe.RelPath,
    ) -> sql.Task:
        sbom_task = sql.Task(
            task_type=sql.TaskType.SBOM_OSV_SCAN,
            task_args=sbom.FileArgs(
                project_key=project_key,
                version_key=version_key,
                revision_number=revision_number,
                file_path=rel_path,
                asf_uid=util.unwrap(self.__asf_uid),
            ).model_dump(),
            asf_uid=util.unwrap(self.__asf_uid),
            added=datetime.datetime.now(datetime.UTC),
            status=sql.TaskStatus.QUEUED,
            project_key=str(project_key),
            version_key=str(version_key),
            revision_number=str(revision_number),
            primary_rel_path=str(rel_path),
        )
        self.__data.add(sbom_task)
        await self.__data.commit()
        await self.__data.refresh(sbom_task)
        return sbom_task


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
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key


def _resolved_path_str(path: pathlib.Path) -> str:
    return str(path.resolve())
