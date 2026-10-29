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
        session = await sessions.read()
        if (
            (not self.__write.authorisation.is_asfquart_session)
            or (session is None)
            or (session.uid != self.__asf_uid)
            or session.admin_uid
            or (session.mfa is not True)
        ):
            raise storage.AccessError("Gate decisions need your own signed in session", status=403)
        not_found = storage.AccessError("Gate request not found", status=404)
        request = await self.__data.gate_request(id=request_id).demand(not_found)
        gate = gates.gate(request.repository_id)
        if gate is None:
            raise not_found
        eligible = await gates.eligible(gate)
        if self.__asf_uid not in eligible:
            raise storage.AccessError("You are not an active member of this repository's release team", status=403)

        await self.__data.begin_immediate()
        self.__data.expire_all()
        request = await self.__data.gate_request(id=request_id).demand(not_found)
        if (request.status != sql.GateStatus.PENDING) or (self.__asf_uid in request.decisions):
            raise storage.AccessError("This request is already decided, or you have already decided it", status=409)
        request.decisions = {**request.decisions, self.__asf_uid: approve}
        request.status = gates.tally(request.decisions, eligible)
        if request.status != sql.GateStatus.PENDING:
            request.decided = datetime.datetime.now(datetime.UTC)
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
