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
import hashlib
from typing import Final

import sqlmodel

import atr.db as db
import atr.jwtoken as jwtoken
import atr.mail as mail
import atr.models.sql as sql
import atr.storage as storage

# TODO: Check that this is known and that its emails are correctly discarded
NOREPLY_EMAIL_ADDRESS: Final[str] = "noreply@apache.org"


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

    async def add_token(
        self, uid: str, token_hash: str, created: datetime.datetime, expires: datetime.datetime, label: str | None
    ) -> sql.PersonalAccessToken:
        if not label:
            raise ValueError("Label is required")
        pat = sql.PersonalAccessToken(
            asfuid=uid,
            token_hash=token_hash,
            created=created,
            expires=expires,
            label=label,
        )
        self.__data.add(pat)
        await self.__data.commit()
        message = mail.Message(
            email_sender=NOREPLY_EMAIL_ADDRESS,
            email_recipient=f"{uid}@apache.org",
            subject="New API Token Created",
            body=f"A new API token called '{label}' was created for your account. "
            "If you did not create this token, please revoke it immediately.",
        )
        await self.__write_as.mail.send(message)
        return pat

    async def delete_token(self, token_id: int) -> None:
        pat = await self.__data.query_one_or_none(
            sqlmodel.select(sql.PersonalAccessToken).where(
                sql.PersonalAccessToken.id == token_id,
                sql.PersonalAccessToken.asfuid == self.__asf_uid,
            )
        )
        if pat is not None:
            await self.__data.delete(pat)
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                token_id=token_id,
            )
            label = pat.label or "[unlabeled]"
            message = mail.Message(
                email_sender=NOREPLY_EMAIL_ADDRESS,
                email_recipient=f"{self.__asf_uid}@apache.org",
                subject="Deleted API Token",
                body=f"An API token called '{label}' was deleted from your account. "
                "If you did not delete this token, please check your account immediately.",
            )
            await self.__write_as.mail.send(message)

    async def issue_jwt(self, pat_text: str) -> str:
        pat_hash = hashlib.sha3_256(pat_text.encode()).hexdigest()
        pat = await self.__data.query_one_or_none(
            sqlmodel.select(sql.PersonalAccessToken).where(
                sql.PersonalAccessToken.asfuid == self.__asf_uid,
                sql.PersonalAccessToken.token_hash == pat_hash,
            )
        )
        if (pat is None) or (pat.expires < datetime.datetime.now(datetime.UTC)):
            raise storage.AccessError("Authentication failed")
        issued_jwt = jwtoken.issue(self.__asf_uid)
        pat.last_used = datetime.datetime.now(datetime.UTC)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            pat_hash=pat_hash,
        )
        return issued_jwt


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
