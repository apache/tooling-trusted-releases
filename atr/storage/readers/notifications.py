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

import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.storage as storage


class FoundationCommitter:
    # We never usually need to authenticate to read, but we do for notifications
    # They're still public, but we need to show them to the correct user
    # TODO: We should probably move all interaction.py stuff to storage anyway

    def __init__(self, read: storage.Read, read_as: storage.ReadAsFoundationCommitter, data: db.Session):
        self.__read = read
        self.__read_as = read_as
        self.__data = data
        self.__asf_uid = read.authorisation.asf_uid

    async def pending(self, limit: int = 50) -> list[sql.Notification]:
        if self.__asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        return await self.__list_for_uid(limit)

    async def __list_for_uid(self, limit: int) -> list[sql.Notification]:
        via = sql.validate_instrumented_attribute
        stmt = (
            sqlmodel.select(sql.Notification)
            .where(sql.Notification.asf_uid == self.__asf_uid)
            .order_by(via(sql.Notification.created), via(sql.Notification.id))
            .limit(limit)
        )
        return await self.__data.query_all(stmt)
