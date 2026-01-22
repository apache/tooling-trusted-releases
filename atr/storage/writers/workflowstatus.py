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

import atr.db as db
import atr.models.sql as sql
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

    # async def add_key(self, key: str, asf_uid: str) -> str:
    #     fingerprint = util.key_ssh_fingerprint(key)
    #     self.__data.add(sql.SSHKey(fingerprint=fingerprint, key=key, asf_uid=asf_uid))
    #     await self.__data.commit()
    #     return fingerprint
    #
    # async def delete_key(self, fingerprint: str) -> None:
    #     ssh_key = await self.__data.ssh_key(
    #         fingerprint=fingerprint,
    #         asf_uid=self.__asf_uid,
    #     ).demand(storage.AccessError(f"Key not found: {fingerprint}"))
    #     await self.__data.delete(ssh_key)
    #     await self.__data.commit()


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

    async def add_workflow_status(
        self,
        workflow_id: str,
        run_id: int,
        project_name: str,
        task_id: int | None = None,
        status: str | None = None,
        message: str | None = None,
    ) -> sql.WorkflowStatus:
        # now = int(time.time())
        # # Twenty minutes to upload all files
        # ttl = 20 * 60
        # expires = now + ttl
        # fingerprint = util.key_ssh_fingerprint(key)
        ws = sql.WorkflowStatus(
            workflow_id=workflow_id,
            run_id=run_id,
            project_name=project_name,
            task_id=task_id,
            status=status or "",
            message=message,
        )
        self.__data.add(ws)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            workflow_id=workflow_id,
            run_id=run_id,
            project_name=project_name,
            task_id=task_id,
            status=status,
            message=message,
        )
        return ws
