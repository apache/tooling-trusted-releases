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

import time

import sqlmodel

import atr.db as db
import atr.mail as mail
import atr.models.github as github
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.util as util


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

    async def add_key(self, key: str) -> str:
        fingerprint = util.key_ssh_fingerprint(key)
        self.__data.add(sql.SSHKey(fingerprint=fingerprint, key=key, asf_uid=self.__asf_uid))
        await self.__data.commit()
        return fingerprint

    async def delete_key(self, fingerprint: str) -> None:
        ssh_key = await self.__data.ssh_key(
            fingerprint=fingerprint,
            asf_uid=self.__asf_uid,
        ).demand(storage.AccessError(f"Key not found: {fingerprint}", status=404))
        await self.__data.delete(ssh_key)
        await self.__data.commit()
        message = mail.Message(
            email_sender=mail.NOREPLY_EMAIL_ADDRESS,
            email_to=f"{self.__asf_uid}@apache.org",
            subject="ATR - Deleted SSH key",
            body=f"In ATR an SSH key with fingerprint '{fingerprint}' was deleted from your account. "
            "If you did not make this change, please contact ASF Tooling.",
        )
        await self.__write_as.mail.send(message, mail.MailFooterCategory.AUTO)


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

    async def add_workflow_key(
        self,
        github_uid: str,
        github_nid: int,
        project_key: safe.ProjectKey,
        key: str,
        github_payload: github.TrustedPublisherPayload,
    ) -> tuple[str, int]:
        now = int(time.time())
        # Twenty minutes to upload all files
        ttl = 20 * 60
        expires = now + ttl
        fingerprint = util.key_ssh_fingerprint(key)
        # Exclude nbf and exp as we've already validated this key - now protected by workflowkey "expires"
        json_payload = github_payload.model_dump(exclude={"exp", "nbf"})
        wsk = sql.WorkflowSSHKey(
            fingerprint=fingerprint,
            key=key,
            project_key=str(project_key),
            asf_uid=self.__asf_uid,
            github_uid=github_uid,
            github_nid=github_nid,
            github_payload=json_payload,
            expires=expires,
        )
        self.__data.add(wsk)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            fingerprint=fingerprint,
            project_key=str(project_key),
            github_uid=github_uid,
            github_nid=github_nid,
            expires=expires,
        )
        return fingerprint, expires


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


class FoundationAdmin(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsFoundationAdmin,
        data: db.Session,
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid

    async def revoke_all_user_keys(self, target_asf_uid: str) -> tuple[int, int]:
        """Revoke all SSH keys for a specified user.

        Returns (persistent_count, workflow_count) of keys affected.
        """
        via = sql.validate_instrumented_attribute
        persistent_keys = await self.__data.query_all(
            sqlmodel.select(sql.SSHKey).where(sql.SSHKey.asf_uid == target_asf_uid)
        )
        persistent_count = len(persistent_keys)
        for key in persistent_keys:
            await self.__data.delete(key)

        workflow_keys = await self.__data.query_all(
            sqlmodel.select(sql.WorkflowSSHKey).where(
                sql.WorkflowSSHKey.asf_uid == target_asf_uid,
                via(sql.WorkflowSSHKey.revoked).is_(False),
            )
        )
        workflow_count = len(workflow_keys)
        for key in workflow_keys:
            key.revoked = True

        total = persistent_count + workflow_count
        if total > 0:
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                target_asf_uid=target_asf_uid,
                persistent_keys_deleted=persistent_count,
                workflow_keys_revoked=workflow_count,
            )
        return persistent_count, workflow_count
