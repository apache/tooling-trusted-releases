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

import datetime

import atr.config as config
import atr.constants as constants
import atr.db as db
import atr.gates as gates
import atr.models.args as args
import atr.models.sql as sql
import atr.sessions as sessions
import atr.storage as storage


class FoundationCommitter:
    def __init__(self, write: storage.Write, write_as: storage.IdentifiedAuditWriteAs, data: db.Session):
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        self.__asf_uid = write_as.asf_uid

    async def decide(self, request_id: int, approve: bool) -> sql.GateStatus:
        await self.__own_session()
        not_found = storage.AccessError("Gate request not found", status=404)
        request = await self.__data.gate_request(id=request_id).demand(not_found)
        gate = await gates.active(self.__data, request.repository_id)
        if gate is None:
            raise not_found
        eligible = await self.__team_member(gate.committee_key)

        await self.__data.begin_immediate()
        self.__data.expire_all()
        request = await self.__data.gate_request(id=request_id).demand(not_found)
        self.__vote(request, approve, eligible)
        if request.status != sql.GateStatus.PENDING:
            self.__data.add(
                sql.Task(
                    task_type=sql.TaskType.GATE_DECISION,
                    task_args=args.GateDecision(request_id=request_id).model_dump(),
                    asf_uid=constants.SYSTEM_SERVICE_UID,
                )
            )
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid, request_id=request_id, approve=approve, status=request.status.value
        )
        return request.status

    async def decide_config(self, config_id: int, approve: bool) -> sql.GateStatus:
        await self.__own_session()
        not_found = storage.AccessError("Gate proposal not found", status=404)
        proposal = await self.__data.gate_config(id=config_id).demand(not_found)
        eligible = await self.__team_member(proposal.committee_key)

        await self.__data.begin_immediate()
        self.__data.expire_all()
        proposal = await self.__data.gate_config(id=config_id).demand(not_found)
        current = await gates.active(self.__data, proposal.repository_id)
        if proposal.previous_id != (None if (current is None) else current.id):
            raise storage.AccessError("The gate has changed since this was proposed", status=409)
        self.__vote(proposal, approve, eligible)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid, config_id=config_id, approve=approve, status=proposal.status.value
        )
        return proposal.status

    async def propose(
        self,
        repository: str,
        committee_key: str,
        environment: str,
        workflow: str,
        event: str,
        ref_type: str,
        ref_pattern: str,
    ) -> None:
        await self.__own_session()
        if (event not in gates.EVENTS) or (ref_type not in gates.REF_TYPES):
            raise storage.AccessError(f"Gates do not support {event} runs on a {ref_type}", status=400)
        if not gates.pattern_supported(ref_pattern):
            raise storage.AccessError("Patterns may only use *, ?, **/ and sets like [0-9] or [abc]", status=400)
        repository_id, repository = await gates.lookup(repository)
        current = await gates.active(self.__data, repository_id)
        if current is not None:
            committee_key = current.committee_key
        owner = f"apache/{committee_key}"
        named = (repository == owner) or repository.startswith(f"{owner}-")
        if (committee_key not in gates.TEAMS) or (config.is_production_mode() and (not named)):
            raise storage.AccessError(f"{repository} cannot have a gate for {committee_key}", status=400)
        await self.__team_member(committee_key)
        proposal = sql.GateConfig(
            repository_id=repository_id,
            repository=repository,
            committee_key=committee_key,
            environment=environment.lower(),
            workflow=workflow,
            event=event,
            ref_type=ref_type,
            ref_pattern=ref_pattern,
            previous_id=None if (current is None) else current.id,
            decisions={self.__asf_uid: True},
        )
        self.__data.add(proposal)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(asf_uid=self.__asf_uid, **proposal.model_dump(exclude={"decided"}))

    async def __own_session(self) -> None:
        session = await sessions.read()
        if (
            (not self.__write.authorisation.is_asfquart_session)
            or (session is None)
            or (session.uid != self.__asf_uid)
            or session.admin_uid
            or (session.mfa is not True)
        ):
            raise storage.AccessError("Gate decisions need your own signed in session", status=403)

    async def __team_member(self, committee_key: str) -> set[str]:
        eligible = await gates.eligible(committee_key)
        if self.__asf_uid not in eligible:
            raise storage.AccessError("You are not an active member of this repository's release team", status=403)
        return eligible

    def __vote(self, row: sql.GateConfig | sql.GateRequest, approve: bool, eligible: set[str]) -> None:
        if (row.status != sql.GateStatus.PENDING) or (self.__asf_uid in row.decisions):
            raise storage.AccessError("This is already decided, or you have already decided it", status=409)
        row.decisions = {**row.decisions, self.__asf_uid: approve}
        row.status = gates.tally(row.decisions, eligible)
        if row.status != sql.GateStatus.PENDING:
            row.decided = datetime.datetime.now(datetime.UTC)
