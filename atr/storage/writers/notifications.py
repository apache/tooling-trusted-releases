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

from typing import Final

import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.storage as storage

_MAX_MESSAGE_LENGTH: Final[int] = 1024


class FoundationCommitter:
    def __init__(self, write: storage.Write, write_as: storage.IdentifiedAuditWriteAs, data: db.Session):
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        self.__asf_uid = write_as.asf_uid

    async def create(
        self,
        message: str,
        level: sql.NotificationLevel = sql.NotificationLevel.ERROR,
        link: str | None = None,
        link_text: str | None = None,
        is_admin: bool = False,
    ) -> sql.Notification | None:
        stmt = sql.notification_insert(
            self.__asf_uid, _normalised_message(message), level, link, link_text, is_admin=is_admin
        ).returning(sql.Notification)
        notification = (await self.__data.execute(stmt)).scalars().one_or_none()
        await self.__data.commit()
        if notification is None:
            return None
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            notification_id=notification.id,
            level=level.value,
        )
        return notification

    async def dismiss(self, notification_id: int) -> bool:
        via = sql.validate_instrumented_attribute
        result = await self.__data.execute(
            sqlmodel.delete(sql.Notification).where(
                via(sql.Notification.id) == notification_id,
                via(sql.Notification.asf_uid) == self.__asf_uid,
            )
        )
        await self.__data.commit()
        dismissed = (getattr(result, "rowcount", 0) or 0) > 0
        if dismissed:
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                notification_id=notification_id,
            )
        return dismissed

    async def replace(
        self,
        message: str | None,
        link: str,
        link_text: str | None = None,
        level: sql.NotificationLevel = sql.NotificationLevel.ERROR,
        is_admin: bool = False,
    ) -> None:
        message = _normalised_message(message) if message is not None else None
        via = sql.validate_instrumented_attribute
        delete_stmt = sqlmodel.delete(sql.Notification).where(
            via(sql.Notification.asf_uid) == self.__asf_uid,
            via(sql.Notification.link) == link,
        )
        if message is not None:
            dedup_hash = sql.notification_dedup_hash(level, message, link, link_text)
            delete_stmt = delete_stmt.where(via(sql.Notification.dedup_hash) != dedup_hash)
        result = await self.__data.execute(delete_stmt)
        notification = None
        if message is not None:
            insert_stmt = sql.notification_insert(
                self.__asf_uid, message, level, link, link_text, is_admin=is_admin
            ).returning(sql.Notification)
            notification = (await self.__data.execute(insert_stmt)).scalars().one_or_none()
        await self.__data.commit()
        removed = getattr(result, "rowcount", 0) or 0
        if removed or (notification is not None):
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                link=link,
                removed=removed,
                notification_id=notification.id if notification is not None else None,
            )


def _normalised_message(message: str) -> str:
    message = " ".join(message.strip().split())
    if not message:
        raise ValueError("Notification message cannot be empty")
    if len(message) > _MAX_MESSAGE_LENGTH:
        return message[: _MAX_MESSAGE_LENGTH - 3] + "..."
    return message
