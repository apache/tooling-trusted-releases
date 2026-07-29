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
import atr.models.sql as sql
import atr.storage as storage

if TYPE_CHECKING:
    import datetime


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

    async def release_manager_add(self, asf_uid: str) -> bool:
        asf_uid = asf_uid.strip().lower()
        if not asf_uid:
            raise storage.AccessError("An ASF UID is required", status=400)
        await self.__data.begin_immediate()
        try:
            committee = await self.__load_committee()
            if asf_uid in committee.committee_members:
                raise storage.AccessError(
                    f"{asf_uid} is a member of the {self.__committee_key} PMC,"
                    " and is therefore already a release manager",
                    status=400,
                )
            if asf_uid not in committee.committers:
                raise storage.AccessError(
                    f"{asf_uid} is not a committer of the {self.__committee_key} committee",
                    status=400,
                )
            if asf_uid in committee.release_managers:
                await self.__data.rollback()
                return False
            # JSON columns do not track in place mutation, so assign a new list
            committee.release_managers = sorted({*committee.release_managers, asf_uid})
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            release_manager=asf_uid,
        )
        return True

    async def release_manager_remove(self, asf_uid: str) -> bool:
        asf_uid = asf_uid.strip().lower()
        await self.__data.begin_immediate()
        try:
            committee = await self.__load_committee()
            if asf_uid not in committee.release_managers:
                await self.__data.rollback()
                return False
            committee.release_managers = [uid for uid in committee.release_managers if uid != asf_uid]
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            release_manager=asf_uid,
        )
        return True

    async def __load_committee(self) -> sql.Committee:
        return await self.__data.committee(key=self.__committee_key).demand(
            storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
        )


class FoundationAdmin(CommitteeMember):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeAdmin,
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

    async def archive(self, archived: datetime.datetime | None) -> None:
        """Retire the committee, dating the retirement where we know the date."""
        await self.__data.begin_immediate()
        try:
            committee = await self.__data.committee(key=self.__committee_key).demand(
                storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
            )
            committee.is_archived = True
            committee.archived = archived
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            archived=archived.isoformat() if (archived is not None) else None,
        )

    async def catalog_reviewed_set(self, reviewed: bool) -> None:
        """Record whether the PMC has reviewed the catalogue we seeded for them."""
        await self.__data.begin_immediate()
        try:
            committee = await self.__data.committee(key=self.__committee_key).demand(
                storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
            )
            committee.catalog_reviewed = reviewed
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            catalog_reviewed=reviewed,
        )

    async def roster_person_remove(self, asf_uid: str) -> None:
        asf_uid = asf_uid.strip().lower()
        await self.__data.begin_immediate()
        try:
            committee = await self.__roster_committee()
            for attr in ("committee_members", "committers", "release_managers"):
                # JSON columns do not track in place mutation, so assign a new list
                setattr(committee, attr, [uid for uid in getattr(committee, attr) if uid != asf_uid])
            await self.__delete_stray_users({asf_uid})
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            roster_uid=asf_uid,
        )

    async def roster_person_set(self, asf_uid: str, fullname: str, pmc: bool) -> None:
        asf_uid = asf_uid.strip().lower()
        if not asf_uid:
            raise storage.AccessError("An ASF UID is required", status=400)
        await self.__data.begin_immediate()
        try:
            committee = await self.__roster_committee()
            committee.committers = sorted({*committee.committers, asf_uid})
            members = {*committee.committee_members, asf_uid} if pmc else {*committee.committee_members} - {asf_uid}
            committee.committee_members = sorted(members)
            user = await self.__data.user(asf_uid=asf_uid).get()
            if user is None:
                self.__data.add(sql.User(asfuid=asf_uid, name=fullname or None))
            elif fullname:
                user.name = fullname
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            roster_uid=asf_uid,
            fullname=fullname,
            pmc=pmc,
        )

    async def roster_reset(self) -> None:
        await self.__data.begin_immediate()
        try:
            committee = await self.__roster_committee()
            removed = {*committee.committee_members, *committee.committers, *committee.release_managers} - {"test"}
            # The pristine roster, as created by server._initialise_test_environment
            committee.committee_members = ["test"]
            committee.committers = ["test"]
            committee.release_managers = ["test"]
            await self.__delete_stray_users(removed)
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            removed=[uid for uid in sorted(removed)],
        )

    async def __delete_stray_users(self, asf_uids: set[str]) -> None:
        committees = await self.__data.committee().all()
        keep = {
            uid
            for committee in committees
            if committee.key != "test"
            for uid in (*committee.committee_members, *committee.committers, *committee.release_managers)
        }
        for asf_uid in asf_uids - keep - {"test"}:
            if user := await self.__data.user(asf_uid=asf_uid).get():
                await self.__data.delete(user)

    async def __roster_committee(self) -> sql.Committee:
        if self.__committee_key != "test":
            raise storage.AccessError("Roster editing is only available for the test committee", status=403)
        return await self.__data.committee(key=self.__committee_key).demand(
            storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
        )
