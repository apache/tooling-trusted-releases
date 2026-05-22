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

from __future__ import annotations

import contextlib
import datetime
import json
import logging
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

import atr.db as db
import atr.log as log
import atr.models.basic as basic
import atr.models.safe as safe
import atr.models.sql as sql
import atr.principal as principal
import atr.storage.outcome as outcome
import atr.storage.readers as readers
import atr.storage.writers as writers
import atr.user as user

# Access

## Access credentials


class AccessAs:
    def append_to_audit_log(self, **kwargs: basic.JSON) -> None:
        audit(**kwargs)


class ReadAs(AccessAs): ...


class WriteAs(AccessAs): ...


# A = TypeVar("A", bound=AccessCredentials)
# R = TypeVar("R", bound=AccessCredentialsRead)
# W = TypeVar("W", bound=AccessCredentialsWrite)

## Access error


class AccessError(RuntimeError):
    def __init__(self, message: str = "Storage access error", status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


# Read


class ReadAsGeneralPublic(ReadAs):
    def __init__(self, read: Read, data: db.Session) -> None:
        self.checks = readers.checks.GeneralPublic(read, self, data)
        self.releases = readers.releases.GeneralPublic(read, self, data)
        self.tokens = readers.tokens.GeneralPublic(read, self, data)


class ReadAsFoundationCommitter(ReadAsGeneralPublic):
    def __init__(self, read: Read, data: db.Session) -> None:
        # self.checks = readers.checks.FoundationCommitter(read, self, data)
        # self.releases = readers.releases.FoundationCommitter(read, self, data)
        self.notifications = readers.notifications.FoundationCommitter(read, self, data)
        self.tokens = readers.tokens.FoundationCommitter(read, self, data)
        self.user = readers.user.FoundationCommitter(read, self, data)


class ReadAsCommitteeParticipant(ReadAsFoundationCommitter): ...


class ReadAsCommitteeMember(ReadAsFoundationCommitter): ...


class Read:
    def __init__(self, authorisation: principal.Authorisation, data: db.Session):
        self.authorisation = authorisation
        self.__data = data

    # @property
    # def asf_uid(self) -> str | None:
    #     return self.authorisation.asf_uid

    def as_foundation_committer(self) -> ReadAsFoundationCommitter:
        return self.as_foundation_committer_outcome().result_or_raise()

    def as_foundation_committer_outcome(self) -> outcome.Outcome[ReadAsFoundationCommitter]:
        try:
            rafc = ReadAsFoundationCommitter(self, self.__data)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(rafc)

    def as_general_public(self) -> ReadAsGeneralPublic:
        return self.as_general_public_outcome().result_or_raise()

    def as_general_public_outcome(self) -> outcome.Outcome[ReadAsGeneralPublic]:
        try:
            ragp = ReadAsGeneralPublic(self, self.__data)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(ragp)


# Write


class WriteAsGeneralPublic(WriteAs):
    def __init__(self, write: Write, data: db.Session):
        self.announce = writers.announce.GeneralPublic(write, self, data)
        self.cache = writers.cache.GeneralPublic(write, self, data)
        self.checks = writers.checks.GeneralPublic(write, self, data)
        self.keys = writers.keys.GeneralPublic(write, self, data)
        self.mail = writers.mail.GeneralPublic(write, self, data)
        self.policy = writers.policy.GeneralPublic(write, self, data)
        self.project = writers.project.GeneralPublic(write, self, data)
        self.release = writers.release.GeneralPublic(write, self, data)
        self.revision = writers.revision.GeneralPublic(write, self, data)
        self.sbom = writers.sbom.GeneralPublic(write, self, data)
        self.ssh = writers.ssh.GeneralPublic(write, self, data)
        self.tokens = writers.tokens.GeneralPublic(write, self, data)
        self.vote = writers.vote.GeneralPublic(write, self, data)


class WriteAsFoundationCommitter(WriteAsGeneralPublic):
    def __init__(self, write: Write, data: db.Session):
        # TODO: We need a definitive list of ASF UIDs
        self.__asf_uid = write.authorisation.asf_uid
        self.announce = writers.announce.FoundationCommitter(write, self, data)
        self.cache = writers.cache.FoundationCommitter(write, self, data)
        self.checks = writers.checks.FoundationCommitter(write, self, data)
        self.keys = writers.keys.FoundationCommitter(write, self, data)
        self.mail = writers.mail.FoundationCommitter(write, self, data)
        self.notifications = writers.notifications.FoundationCommitter(write, self, data)
        self.policy = writers.policy.FoundationCommitter(write, self, data)
        self.project = writers.project.FoundationCommitter(write, self, data)
        self.release = writers.release.FoundationCommitter(write, self, data)
        self.revision = writers.revision.FoundationCommitter(write, self, data)
        self.sbom = writers.sbom.FoundationCommitter(write, self, data)
        self.ssh = writers.ssh.FoundationCommitter(write, self, data)
        self.tokens = writers.tokens.FoundationCommitter(write, self, data)
        self.user = writers.user.FoundationCommitter(write, self, data)
        self.vote = writers.vote.FoundationCommitter(write, self, data)

    @property
    def asf_uid(self) -> str:
        if self.__asf_uid is None:
            raise AccessError("Not authorized", status=403)
        return self.__asf_uid


class WriteAsCommitteeParticipant(WriteAsFoundationCommitter):
    def __init__(self, write: Write, data: db.Session, committee_key: str):
        self.__asf_uid = write.authorisation.asf_uid
        self.__committee_key = committee_key
        self.announce = writers.announce.CommitteeParticipant(write, self, data, committee_key)
        self.cache = writers.cache.CommitteeParticipant(write, self, data, committee_key)
        self.checks = writers.checks.CommitteeParticipant(write, self, data, committee_key)
        self.keys = writers.keys.CommitteeParticipant(write, self, data, committee_key)
        self.mail = writers.mail.CommitteeParticipant(write, self, data, committee_key)
        self.policy = writers.policy.CommitteeParticipant(write, self, data, committee_key)
        self.project = writers.project.CommitteeParticipant(write, self, data, committee_key)
        self.release = writers.release.CommitteeParticipant(write, self, data, committee_key)
        self.revision = writers.revision.CommitteeParticipant(write, self, data, committee_key)
        self.sbom = writers.sbom.CommitteeParticipant(write, self, data, committee_key)
        self.ssh = writers.ssh.CommitteeParticipant(write, self, data, committee_key)
        self.tokens = writers.tokens.CommitteeParticipant(write, self, data, committee_key)
        self.vote = writers.vote.CommitteeParticipant(write, self, data, committee_key)

    @property
    def asf_uid(self) -> str:
        if self.__asf_uid is None:
            raise AccessError("Not authorized", status=403)
        return self.__asf_uid

    @property
    def committee_key(self) -> str:
        return self.__committee_key


class WriteAsReleaseManager(WriteAsCommitteeParticipant):
    def __init__(self, write: Write, data: db.Session, committee_key: str):
        super().__init__(write, data, committee_key)
        self.vote = writers.vote.ReleaseManager(write, self, data, committee_key)


class WriteAsCommitteeMember(WriteAsReleaseManager):
    def __init__(self, write: Write, data: db.Session, committee_key: str):
        self.__asf_uid = write.authorisation.asf_uid
        self.__committee_key = committee_key
        super().__init__(write, data, committee_key)
        self.announce = writers.announce.CommitteeMember(write, self, data, committee_key)
        self.cache = writers.cache.CommitteeMember(write, self, data, committee_key)
        self.checks = writers.checks.CommitteeMember(write, self, data, committee_key)
        self.distributions = writers.distributions.CommitteeMember(write, self, data, committee_key)
        self.keys = writers.keys.CommitteeMember(write, self, data, committee_key)
        self.mail = writers.mail.CommitteeMember(write, self, data, committee_key)
        self.policy = writers.policy.CommitteeMember(write, self, data, committee_key)
        self.project = writers.project.CommitteeMember(write, self, data, committee_key)
        self.release = writers.release.CommitteeMember(write, self, data, committee_key)
        self.revision = writers.revision.CommitteeMember(write, self, data, committee_key)
        self.sbom = writers.sbom.CommitteeMember(write, self, data, committee_key)
        self.ssh = writers.ssh.CommitteeMember(write, self, data, committee_key)
        self.tokens = writers.tokens.CommitteeMember(write, self, data, committee_key)
        self.vote = writers.vote.CommitteeMember(write, self, data, committee_key)
        self.workflowstatus = writers.workflowstatus.CommitteeMember(write, self, data, committee_key)

    @property
    def asf_uid(self) -> str:
        if self.__asf_uid is None:
            raise AccessError("Not authorized", status=403)
        return self.__asf_uid

    @property
    def committee_key(self) -> str:
        return self.__committee_key


class WriteAsFoundationAdmin(WriteAsFoundationCommitter):
    def __init__(self, write: Write, data: db.Session):
        # __asf_uid and the asf_uid property are inherited from
        # WriteAsFoundationCommitter. Defining our own here previously caused
        # an AttributeError during construction: super().__init__() creates
        # writers (notably notifications) that read write_as.asf_uid, which
        # via MRO resolves to this class's property override, which reads
        # _WriteAsFoundationAdmin__asf_uid, which had not yet been assigned.
        super().__init__(write, data)
        self.release = writers.release.FoundationAdmin(write, self, data)
        self.tokens = writers.tokens.FoundationAdmin(write, self, data)
        self.ssh = writers.ssh.FoundationAdmin(write, self, data)


class WriteAsCommitteeAdmin(WriteAsCommitteeMember):
    def __init__(self, write: Write, data: db.Session, committee_key: str):
        super().__init__(write, data, committee_key)
        self.keys = writers.keys.FoundationAdmin(write, self, data, committee_key)


class WriteAsUserService(WriteAs):
    def __init__(self, data: db.Session, asf_uid: str):
        asf_uid = asf_uid.strip()
        if not asf_uid:
            raise AccessError("User service writes require an ASF UID", status=500)
        self.__data = data
        self.__asf_uid = asf_uid
        self.notifications = writers.notifications.UserService(self, data)

    @property
    def asf_uid(self) -> str:
        return self.__asf_uid


# TODO: Could name this WriteDispatcher
class Write:
    # Read and Write have authenticator methods which return access outcomes
    # TODO: Still need to send some runtime credentials guarantee to the WriteAs* classes
    def __init__(self, authorisation: principal.Authorisation, data: db.Session):
        self.__authorisation: Final[principal.Authorisation] = authorisation
        self.__data: Final[db.Session] = data

    @property
    def authorisation(self) -> principal.Authorisation:
        return self.__authorisation

    def as_committee_admin(self, committee_key: str) -> WriteAsCommitteeAdmin:
        return self.as_committee_admin_outcome(committee_key).result_or_raise()

    def as_committee_admin_outcome(self, committee_key: str) -> outcome.Outcome[WriteAsCommitteeAdmin]:
        if self.__authorisation.asf_uid is None:
            return outcome.Error(AccessError("Not authorized", status=403))
        if not user.is_admin(self.__authorisation.asf_uid):
            return outcome.Error(AccessError("Not an admin", status=403))
        try:
            waca = WriteAsCommitteeAdmin(self, self.__data, committee_key)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(waca)

    def as_committee_member(self, committee_key: str) -> WriteAsCommitteeMember:
        return self.as_committee_member_outcome(committee_key).result_or_raise()

    def as_committee_member_outcome(self, committee_key: str) -> outcome.Outcome[WriteAsCommitteeMember]:
        if self.__authorisation.asf_uid is None:
            return outcome.Error(AccessError("Not authorized", status=403))
        if not self.__authorisation.is_member_of(committee_key):
            return outcome.Error(
                AccessError(f"{self.__authorisation.asf_uid} is not a member of {committee_key}", status=403)
            )
        try:
            wacm = WriteAsCommitteeMember(self, self.__data, committee_key)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(wacm)

    def as_committee_participant(self, committee_key: str) -> WriteAsCommitteeParticipant:
        return self.as_committee_participant_outcome(committee_key).result_or_raise()

    def as_committee_participant_outcome(self, committee_key: str) -> outcome.Outcome[WriteAsCommitteeParticipant]:
        if self.__authorisation.asf_uid is None:
            return outcome.Error(AccessError("Not authorized", status=403))
        if not self.__authorisation.is_participant_of(committee_key):
            return outcome.Error(AccessError(f"Not a participant of {committee_key}", status=403))
        try:
            wacp = WriteAsCommitteeParticipant(self, self.__data, committee_key)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(wacp)

    def as_foundation_committer(self) -> WriteAsFoundationCommitter:
        return self.as_foundation_committer_outcome().result_or_raise()

    def as_foundation_committer_outcome(self) -> outcome.Outcome[WriteAsFoundationCommitter]:
        if self.__authorisation.asf_uid is None:
            return outcome.Error(AccessError("Not authorized", status=403))
        try:
            wafm = WriteAsFoundationCommitter(self, self.__data)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(wafm)

    def as_foundation_admin(self) -> WriteAsFoundationAdmin:
        return self.as_foundation_admin_outcome().result_or_raise()

    def as_foundation_admin_outcome(self) -> outcome.Outcome[WriteAsFoundationAdmin]:
        if self.__authorisation.asf_uid is None:
            return outcome.Error(AccessError("Not authorized", status=403))
        if not user.is_admin(self.__authorisation.asf_uid):
            return outcome.Error(AccessError("Not an admin", status=403))
        try:
            wafa = WriteAsFoundationAdmin(self, self.__data)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(wafa)

    def as_general_public(self) -> WriteAsGeneralPublic:
        return self.as_general_public_outcome().result_or_raise()

    def as_general_public_outcome(self) -> outcome.Outcome[WriteAsGeneralPublic]:
        try:
            wagp = WriteAsGeneralPublic(self, self.__data)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(wagp)

    # async def as_key_owner(self) -> types.Outcome[WriteAsKeyOwner]:
    #     ...

    async def as_project_committee_admin(self, project_key: safe.ProjectKey) -> WriteAsCommitteeAdmin:
        write_as_outcome = await self.as_project_committee_admin_outcome(project_key)
        return write_as_outcome.result_or_raise()

    async def as_project_committee_admin_outcome(
        self, project_key: safe.ProjectKey
    ) -> outcome.Outcome[WriteAsCommitteeAdmin]:
        project = await self.__data.project(str(project_key), _committee=True).demand(
            AccessError(f"Project not found: {project_key}", status=404)
        )
        if project.committee is None:
            return outcome.Error(AccessError("No committee found for project - Invalid state", status=500))
        if self.__authorisation.asf_uid is None:
            return outcome.Error(AccessError("Not authorized", status=403))
        if not user.is_admin(self.__authorisation.asf_uid):
            return outcome.Error(AccessError("Not an admin", status=403))
        try:
            waca = WriteAsCommitteeAdmin(self, self.__data, project.committee.key)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(waca)

    async def as_project_committee_member(self, project_key: safe.ProjectKey) -> WriteAsCommitteeMember:
        write_as_outcome = await self.as_project_committee_member_outcome(project_key)
        return write_as_outcome.result_or_raise()

    async def as_project_committee_member_outcome(
        self, project_key: safe.ProjectKey
    ) -> outcome.Outcome[WriteAsCommitteeMember]:
        project = await self.__data.project(str(project_key), _committee=True).demand(
            AccessError(f"Project not found: {project_key}", status=404)
        )
        if project.committee is None:
            return outcome.Error(AccessError("No committee found for project - Invalid state", status=500))
        if self.__authorisation.asf_uid is None:
            return outcome.Error(AccessError("Not authorized", status=403))
        if not self.__authorisation.is_member_of(project.committee.key):
            return outcome.Error(AccessError(f"Not a member of {project.committee.key}", status=403))
        try:
            wacm = WriteAsCommitteeMember(self, self.__data, project.committee.key)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(wacm)

    async def as_project_committee_participant(self, project_key: safe.ProjectKey) -> WriteAsCommitteeParticipant:
        write_as_outcome = await self.as_project_committee_participant_outcome(project_key)
        return write_as_outcome.result_or_raise()

    async def as_project_committee_participant_outcome(
        self, project_key: safe.ProjectKey
    ) -> outcome.Outcome[WriteAsCommitteeParticipant]:
        project = await self.__data.project(str(project_key), _committee=True).demand(
            AccessError(f"Project not found: {project_key!s}", status=404)
        )
        if project.committee is None:
            return outcome.Error(AccessError("No committee found for project - Invalid state", status=500))
        if self.__authorisation.asf_uid is None:
            return outcome.Error(AccessError("Not authorized", status=403))
        if not self.__authorisation.is_participant_of(project.committee.key):
            return outcome.Error(AccessError(f"Not a participant of {project.committee.key}", status=403))
        try:
            wacp = WriteAsCommitteeParticipant(self, self.__data, project.committee.key)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(wacp)

    async def as_project_release_manager(self, project_key: safe.ProjectKey) -> WriteAsReleaseManager:
        write_as_outcome = await self.as_project_release_manager_outcome(project_key)
        return write_as_outcome.result_or_raise()

    async def as_project_release_manager_outcome(
        self, project_key: safe.ProjectKey
    ) -> outcome.Outcome[WriteAsReleaseManager]:
        project = await self.__data.project(str(project_key), _committee=True).demand(
            AccessError(f"Project not found: {project_key}", status=404)
        )
        if project.committee is None:
            return outcome.Error(AccessError("No committee found for project - Invalid state", status=500))
        asf_uid = self.__authorisation.asf_uid
        if asf_uid is None:
            return outcome.Error(AccessError("Not authorized", status=403))
        is_pmc_member = self.__authorisation.is_member_of(project.committee.key)
        is_designated_release_manager = (asf_uid in project.committee.release_managers) and (
            asf_uid in project.committee.committers
        )
        if (not is_pmc_member) and (not is_designated_release_manager):
            return outcome.Error(AccessError(f"Not a release manager for {project.committee.key}", status=403))
        try:
            warm = WriteAsReleaseManager(self, self.__data, project.committee.key)
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(warm)

    @property
    def member_of(self) -> frozenset[str]:
        return self.__authorisation.member_of()

    async def member_of_committees(self) -> list[sql.Committee]:
        names = list(self.__authorisation.member_of())
        committees = list(await self.__data.committee(name_in=names).all())
        committees.sort(key=lambda c: c.key)
        # Return even standing committees
        return committees

    @property
    def participant_of(self) -> frozenset[str]:
        return self.__authorisation.participant_of()

    async def participant_of_committees(self) -> list[sql.Committee]:
        names = list(self.__authorisation.participant_of())
        committees = list(await self.__data.committee(name_in=names).all())
        committees.sort(key=lambda c: c.key)
        # Return even standing committees
        return committees


# Do not rename this interface
# It is named to reserve the atr.storage.audit logger name
def audit(**kwargs: basic.JSON) -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds")
    now = now.replace("+00:00", "Z")
    action = log.caller_name(depth=2)
    request_user_id = log.get_context("user_id")
    admin_user_id = log.get_context("admin_id")
    kwargs = {"datetime": now, "action": action, **kwargs}
    if request_user_id:
        kwargs["request_user_id"] = request_user_id
    if admin_user_id and (request_user_id != admin_user_id):
        kwargs["admin_user_id"] = admin_user_id
    msg = json.dumps(kwargs, allow_nan=False)
    # The atr.log logger should give the same name
    # But to be extra sure, we set it manually
    logger = logging.getLogger("atr.storage.audit")
    # TODO: Convert to async
    logger.info(msg)


def ensure_project_active(project: sql.Project) -> None:
    if project.status == sql.ProjectStatus.ACTIVE:
        return
    raise AccessError(f"Project '{project.key}' is archived; release actions are disabled.")


@contextlib.asynccontextmanager
async def read(asf_uid: principal.UID = principal.ArgumentNone) -> AsyncGenerator[Read]:
    if asf_uid is principal.ArgumentNone:
        authorisation = await principal.Authorisation()
    else:
        authorisation = await principal.Authorisation(asf_uid)
    async with db.session() as data:
        # TODO: Replace data with a DatabaseReader instance
        yield Read(authorisation, data)


@contextlib.asynccontextmanager
async def read_and_write(asf_uid: principal.UID = principal.ArgumentNone) -> AsyncGenerator[tuple[Read, Write]]:
    if asf_uid is principal.ArgumentNone:
        authorisation = await principal.Authorisation()
    else:
        authorisation = await principal.Authorisation(asf_uid)
    async with db.session() as data:
        # TODO: Replace data with a DatabaseWriter instance
        r = Read(authorisation, data)
        w = Write(authorisation, data)
        yield r, w


@contextlib.asynccontextmanager
async def read_as_foundation_committer(
    asf_uid: principal.UID = principal.ArgumentNone,
) -> AsyncGenerator[ReadAsFoundationCommitter]:
    async with read(asf_uid) as r:
        yield r.as_foundation_committer()


@contextlib.asynccontextmanager
async def read_as_general_public(
    asf_uid: principal.UID = principal.ArgumentNone,
) -> AsyncGenerator[ReadAsGeneralPublic]:
    async with read(asf_uid) as r:
        yield r.as_general_public()


@contextlib.asynccontextmanager
async def write(asf_uid: principal.UID = principal.ArgumentNone) -> AsyncGenerator[Write]:
    if asf_uid is principal.ArgumentNone:
        authorisation = await principal.Authorisation()
    else:
        authorisation = await principal.Authorisation(asf_uid)
    async with db.session() as data:
        # TODO: Replace data with a DatabaseWriter instance
        yield Write(authorisation, data)


@contextlib.asynccontextmanager
async def write_as_committee_member(
    committee_key: str,
    asf_uid: principal.UID = principal.ArgumentNone,
) -> AsyncGenerator[WriteAsCommitteeMember]:
    async with write(asf_uid) as w:
        yield w.as_committee_member(committee_key)


@contextlib.asynccontextmanager
async def write_as_committee_participant(
    committee_key: str,
    asf_uid: principal.UID = principal.ArgumentNone,
) -> AsyncGenerator[WriteAsCommitteeParticipant]:
    async with write(asf_uid) as w:
        yield w.as_committee_participant(committee_key)


@contextlib.asynccontextmanager
async def write_as_project_committee_member(
    project_key: safe.ProjectKey,
    asf_uid: principal.UID = principal.ArgumentNone,
) -> AsyncGenerator[WriteAsCommitteeMember]:
    async with write(asf_uid) as w:
        yield await w.as_project_committee_member(project_key)


@contextlib.asynccontextmanager
async def write_as_project_release_manager(
    project_key: safe.ProjectKey,
    asf_uid: principal.UID = principal.ArgumentNone,
) -> AsyncGenerator[WriteAsReleaseManager]:
    async with write(asf_uid) as w:
        yield await w.as_project_release_manager(project_key)


@contextlib.asynccontextmanager
async def write_as_user_service(asf_uid: str) -> AsyncGenerator[WriteAsUserService]:
    async with db.session() as data:
        waus = WriteAsUserService(data, asf_uid)
        yield waus
