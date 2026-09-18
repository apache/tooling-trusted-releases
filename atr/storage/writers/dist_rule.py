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

import atr.db as db
import atr.models.sql as sql
import atr.storage as storage


class FoundationAdmin:
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationAdmin, data: db.Session):
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid

    async def add(
        self,
        kind: sql.DistRuleKind,
        committee: str | None,
        subproject: str | None,
        pattern: str | None,
        target: str | None,
        note: str | None,
    ) -> sql.DistRule:
        row = sql.DistRule(
            kind=kind,
            committee=committee,
            subproject=subproject,
            pattern=pattern,
            target=target,
            enabled=True,
            note=note,
        )
        self.__data.add(row)
        await self.__data.commit()
        await self.__data.refresh(row)
        self.__write_as.append_to_audit_log(asf_uid=self.__asf_uid, action="add", rule_id=row.id, kind=str(kind))
        return row

    async def all_rules(self) -> list[sql.DistRule]:
        via = sql.validate_instrumented_attribute
        rows = await self.__data.dist_rule().order_by(via(sql.DistRule.kind), via(sql.DistRule.id)).all()
        return list(rows)

    async def delete(self, rule_id: int) -> None:
        row = await self.__demand(rule_id)
        await self.__data.delete(row)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(asf_uid=self.__asf_uid, action="delete", rule_id=rule_id)

    async def set_enabled(self, rule_id: int, enabled: bool) -> sql.DistRule:
        row = await self.__demand(rule_id)
        row.enabled = enabled
        self.__data.add(row)
        await self.__data.commit()
        await self.__data.refresh(row)
        state = "enable" if enabled else "disable"
        self.__write_as.append_to_audit_log(asf_uid=self.__asf_uid, action=state, rule_id=rule_id)
        return row

    async def __demand(self, rule_id: int) -> sql.DistRule:
        error = storage.AccessError(f"Dist rule {rule_id} not found.", status=404)
        return await self.__data.dist_rule(id=rule_id).demand(error)
