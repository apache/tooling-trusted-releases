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

import atr.constants as constants
import atr.db as db
import atr.jwtoken as jwtoken
import atr.ldap as ldap
import atr.log as log
import atr.mail as mail
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.datatypes as datatypes


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
    ) -> datatypes.PersonalAccessTokenSafe:
        if not label:
            raise ValueError("Label is required")
        pat = sql.PersonalAccessToken(
            asfuid=self.__asf_uid,
            created_by=self.__asf_uid,
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
        return datatypes.PersonalAccessTokenSafe.from_sql(pat)

    # audit_guidance PAT deletion revokes associated JWTs
    # audit_guidance JWT verification rechecks PAT existence on every API request
    async def delete_token(self, token_id: int) -> None:
        pat = await self.__data.personal_access_token(id=token_id, asfuid=self.__asf_uid).get()
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

    async def issue_jwt(self, pat_text: str, client_ip: str | None) -> str:
        pat_hash = hash_pat(pat_text)
        pat = await self.__data.personal_access_token(pat_hash, asfuid=self.__asf_uid).get()
        if (pat is None) or pat.is_expired or (not pat.allows_ip(client_ip)):
            log.warning(
                "Authentication failed",
                extra={
                    "reason": "invalid_or_expired_pat",
                },
            )
            raise storage.AccessError("Authentication failed", status=401)

        # Verify account still exists in LDAP and is not banned. is_active handles test mode internally,
        # so the explicit test-mode bypass we used to carry here is no longer needed
        if not await ldap.is_active(self.__asf_uid):
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

    async def add_system_token(
        self,
        token_hash: str,
        created: datetime.datetime,
        expires: datetime.datetime,
        label: str,
        allowed_ip: str | None,
    ) -> datatypes.PersonalAccessTokenSafe:
        if not label:
            raise ValueError("Label is required")
        # asfuid stays null so a system PAT has no owning user; created_by
        # records the minting admin so a leaver's tokens can still be revoked.
        pat = sql.PersonalAccessToken(
            asfuid=None,
            created_by=self.__asf_uid,
            token_hash=token_hash,
            created=created,
            expires=expires,
            label=label,
            is_system=True,
            allowed_ip=allowed_ip,
        )
        self.__data.add(pat)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            pat_hash=token_hash,
            is_system=True,
            allowed_ip=allowed_ip,
        )
        log.auth_event("system_pat_issuance", self.__asf_uid, pat_hash=token_hash, name=label)
        return datatypes.PersonalAccessTokenSafe.from_sql(pat)

    async def list_system_tokens(self) -> list[datatypes.PersonalAccessTokenSafe]:
        tokens = await (
            self.__data.personal_access_token(is_system=True)
            .order_by(sql.validate_instrumented_attribute(sql.PersonalAccessToken.created))
            .all()
        )
        return [datatypes.PersonalAccessTokenSafe.from_sql(token) for token in tokens]

    async def revoke_all_user_tokens(self, target_asf_uid: str) -> int:
        """Revoke all PATs that a specified user owns or created. Returns count of revoked tokens."""
        # OR on created_by so a user's system PATs go too.
        via = sql.validate_instrumented_attribute
        stmt = sqlmodel.select(sql.PersonalAccessToken).where(
            sqlmodel.or_(
                via(sql.PersonalAccessToken.asfuid) == target_asf_uid,
                via(sql.PersonalAccessToken.created_by) == target_asf_uid,
            )
        )
        tokens = await self.__data.query_all(stmt)
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

    async def revoke_system_token(self, token_id: int) -> bool:
        pat = await self.__data.personal_access_token(id=token_id, is_system=True).get()
        if pat is None:
            return False
        await self.__data.delete(pat)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            pat_hash=pat.token_hash,
            token_id=token_id,
            is_system=True,
        )
        log.auth_event("system_pat_revoke", self.__asf_uid, pat_hash=pat.token_hash, name=pat.label)
        return True

    async def rotate_jwt_signing_key(self) -> None:
        key = await asyncio.to_thread(jwtoken.write_new_signing_key)
        log.auth_event("jwt_key_rotation", self.__asf_uid)
        jwtoken.activate_signing_key(key)
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            action="rotate_jwt_signing_key",
        )


class SystemService:
    def __init__(self, write_as: storage.WriteAsSystemService, data: db.Session):
        self.__write_as = write_as
        self.__data = data
        self.__asf_uid = write_as.asf_uid

    async def issue_jwt(self, pat_text: str, client_ip: str | None) -> str:
        # No asfuid filter; a system PAT has no owning user.
        pat_hash = hash_pat(pat_text)
        pat = await self.__data.personal_access_token(pat_hash, is_system=True).get()
        # No LDAP check; the is_system match and fixed service identity are the gate.
        if (
            (pat is None)
            or (self.__asf_uid != constants.SYSTEM_SERVICE_UID)
            or pat.is_expired
            or (not pat.allows_ip(client_ip))
        ):
            log.warning(
                "Authentication failed",
                extra={
                    "reason": "invalid_or_expired_system_pat",
                },
            )
            raise storage.AccessError("Authentication failed", status=401)

        issued_jwt = jwtoken.issue(constants.SYSTEM_SERVICE_UID, pat_hash=pat_hash, system=True)
        log.auth_event(
            "jwt_issued", constants.SYSTEM_SERVICE_UID, pat_hash=pat_hash, pat_owner=pat.created_by, name=pat.label
        )
        pat.last_used = datetime.datetime.now(datetime.UTC)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=constants.SYSTEM_SERVICE_UID,
            pat_hash=pat_hash,
            pat_owner=pat.created_by,
            name=pat.label,
        )
        return issued_jwt


def hash_pat(pat_text: str) -> str:
    return hashlib.sha3_256(pat_text.encode()).hexdigest()
