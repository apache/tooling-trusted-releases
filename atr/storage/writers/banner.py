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

HISTORY_LIMIT = 10


class FoundationAdmin:
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationAdmin, data: db.Session):
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid

    async def latest_and_history(self) -> tuple[sql.Banner | None, list[sql.Banner]]:
        via = sql.validate_instrumented_attribute
        rows = await self.__data.banner().order_by(via(sql.Banner.id).desc()).limit(HISTORY_LIMIT + 1).all()
        if not rows:
            return None, []
        return rows[0], list(rows[1:])

    async def restore(self, banner_id: int) -> sql.Banner:
        error = storage.AccessError(f"Banner {banner_id} not found.", status=404)
        row = await self.__data.banner(id=banner_id).demand(error)
        return await self.set_current(row.markdown)

    async def set_current(self, markdown: str) -> sql.Banner:
        row = sql.Banner(markdown=markdown, asf_uid=self.__asf_uid)
        self.__data.add(row)
        await self.__data.commit()
        await self.__data.refresh(row)
        self.__write_as.append_to_audit_log(asf_uid=self.__asf_uid, banner_id=row.id, markdown=markdown)
        return row
