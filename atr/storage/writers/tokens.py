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
import hashlib

import sqlmodel

import atr.db as db
import atr.jwtoken as jwtoken
import atr.ldap as ldap
import atr.log as log
import atr.mail as mail
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.types as types


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

    async def add_token(
        self, token_hash: str, created: datetime.datetime, expires: datetime.datetime, label: str | None
    ) -> types.PersonalAccessTokenSafe:
        if not label:
            raise ValueError("Label is required")
        pat = sql.PersonalAccessToken(
            asfuid=self.__asf_uid,
            token_hash=token_hash,
            created=created,
            expires=expires,
            label=label,
        )
        self.__data.add(pat)
        await self.__data.commit()
        log.auth_event("pat_issuance", self.__asf_uid, pat_hash=pat.token_hash)
        message = mail.Message(
            email_sender=mail.NOREPLY_EMAIL_ADDRESS,
            email_to=f"{self.__asf_uid}@apache.org",
            subject="ATR - New API Token Created",
            body=f"In ATR a new API token called '{label}' was created for your account. "
            "If you did not create this token, please revoke it immediately.",
        )
        await self.__write_as.mail.send(message, mail.MailFooterCategory.AUTO)
        return types.PersonalAccessTokenSafe.from_sql(pat)

    # audit_guidance PAT deletion revokes associated JWTs
    # audit_guidance JWT verification rechecks PAT existence on every API request
    async def delete_token(self, token_id: int) -> None:
        pat: sql.PersonalAccessToken | None = await self.__data.query_one_or_none(
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
            log.auth_event("pat_deleted", self.__asf_uid, pat_hash=pat.token_hash)
            label = pat.label or "[unlabeled]"
            message = mail.Message(
                email_sender=mail.NOREPLY_EMAIL_ADDRESS,
                email_to=f"{self.__asf_uid}@apache.org",
                subject="ATR - Deleted API Token",
                body=f"In ATR an API token called '{label}' was deleted from your account. "
                "If you did not delete this token, please check your account immediately.",
            )
            await self.__write_as.mail.send(message, mail.MailFooterCategory.AUTO)

    async def issue_jwt(self, pat_text: str) -> str:
        pat_hash = hashlib.sha3_256(pat_text.encode()).hexdigest()
        pat = await self.__data.query_one_or_none(
            sqlmodel.select(sql.PersonalAccessToken).where(
                sql.PersonalAccessToken.asfuid == self.__asf_uid,
                sql.PersonalAccessToken.token_hash == pat_hash,
            )
        )
        if (pat is None) or (pat.expires < datetime.datetime.now(datetime.UTC)):
            log.warning(
                "Authentication failed",
                extra={
                    "reason": "invalid_or_expired_pat",
                },
            )
            raise storage.AccessError("Authentication failed", status=401)

        # Verify account still exists in LDAP
        account_details = await ldap.account_lookup(self.__asf_uid)
        if (account_details is None) or ldap.is_banned(account_details):
            log.auth_failure("jwt_issuance", "account_deleted_or_banned", self.__asf_uid)
            raise storage.AccessError("Authentication failed", status=401)

        issued_jwt = jwtoken.issue(self.__asf_uid, pat_hash=pat_hash)
        log.auth_event("jwt_issued", self.__asf_uid, pat_hash=pat_hash)
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

    async def revoke_all_user_tokens(self, target_asf_uid: str) -> int:
        """Revoke all PATs for a specified user. Returns count of revoked tokens."""
        tokens = await self.__data.query_all(
            sqlmodel.select(sql.PersonalAccessToken).where(sql.PersonalAccessToken.asfuid == target_asf_uid)
        )
        count = len(tokens)
        for token in tokens:
            await self.__data.delete(token)

        if count > 0:
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                target_asf_uid=target_asf_uid,
                tokens_revoked=count,
            )
            log.auth_event("pat_bulk_revoke", target_asf_uid, by=self.__asf_uid)
            message = mail.Message(
                email_sender=mail.NOREPLY_EMAIL_ADDRESS,
                email_to=f"{target_asf_uid}@apache.org",
                subject="ATR - Security alert: API tokens revoked by administrator",
                body=f"An administrator has revoked all API tokens ({count}) for your ATR account. "
                "If you did not expect this action, please contact ASF Tooling.",
            )
            await self.__write_as.mail.send(message, mail.MailFooterCategory.AUTO)
        return count

    async def rotate_jwt_signing_key(self) -> None:
        key = await asyncio.to_thread(jwtoken.write_new_signing_key)
        log.auth_event("jwt_key_rotation", self.__asf_uid)
        jwtoken.activate_signing_key(key)
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            action="rotate_jwt_signing_key",
        )
