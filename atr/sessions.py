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

import time
from typing import Any, Final, Literal, get_args

import asfquart.session
import quart
import sqlmodel

import atr.config as config
import atr.db as db
import atr.log as log
import atr.models.sql as sql

MutableSessionField = Literal["downgrade_admin_to_user", "last_account_check"]

_MUTABLE_SESSION_FIELDS: Final[set[str]] = set(get_args(MutableSessionField))
_SESSION_DATA_FIELDS: Final[set[str]] = {f for f in sql.UserSession.model_fields if f not in {"sid_hash", "cts"}}
_SESSION_IDLE_TIMEOUT: Final[int] = 86400 * 7

if not _MUTABLE_SESSION_FIELDS.issubset(sql.UserSession.model_fields):
    raise RuntimeError(
        f"MutableSessionField contains unknown fields: {_MUTABLE_SESSION_FIELDS - set(sql.UserSession.model_fields)}"
    )


async def deleted_or_banned(uid: str) -> None:
    log.auth_failure("oauth", "account_deleted_or_banned", uid)
    await asfquart.APP.sessions.revoke_by_uid(uid)
    await asfquart.session.aclear()
    invalidate_cache()


def invalidate_cache() -> None:
    quart.g._user_session = None


async def read() -> sql.UserSession | None:
    cached = getattr(quart.g, "_user_session", None)
    if isinstance(cached, sql.UserSession):
        return cached
    result = await asfquart.session.read()
    if isinstance(result, sql.UserSession):
        quart.g._user_session = result
        return result
    return None


def _prepare_session_data(session_data: dict[str, Any]) -> dict[str, Any]:
    # Field names match the raw OAuth dict from asfquart
    # They do not match ClientSession attribute names
    # ClientSession remaps on construction
    # Raw "pmcs" becomes ClientSession.committees
    # Raw "roleaccount" becomes ClientSession.isRole
    # Since awrite() passes the raw dict, we map from the raw key names here
    data = dict(session_data)
    if "pmcs" in data:
        data["committees"] = data.pop("pmcs")
    if "isMember" in data:
        data["is_member"] = data.pop("isMember")
    if "isChair" in data:
        data["is_chair"] = data.pop("isChair")
    # if "isRoot" in data:
    #     data["is_root"] = data.pop("isRoot")
    if "roleaccount" in data:
        data["is_role"] = data.pop("roleaccount")
    data.pop("metadata", None)
    data.pop("cts", None)
    data.pop("uts", None)
    return {k: v for k, v in data.items() if k in _SESSION_DATA_FIELDS}


class Store:
    async def create(self, hsid: str, session_data: dict[str, Any]) -> None:
        prepared = _prepare_session_data(session_data)
        now = time.time()
        user_session = sql.UserSession(sid_hash=hsid, cts=now, uts=now, **prepared)
        async with db.session() as data:
            data.add(user_session)
            await data.commit()

    async def destroy(self, hsid: str) -> None:
        async with db.session() as data:
            statement = sqlmodel.select(sql.UserSession).where(sql.UserSession.sid_hash == hsid)
            result = await data.execute(statement)
            user_session = result.scalar_one_or_none()
            if user_session is not None:
                await data.delete(user_session)
                await data.commit()

    async def register(self, hsid: str, user_session: sql.UserSession) -> None:
        now = time.time()
        user_session.sid_hash = hsid
        user_session.cts = now
        user_session.uts = now
        async with db.session() as data:
            data.add(user_session)
            await data.commit()

    async def replace(self, old_hsid: str | None, new_hsid: str, user_session: sql.UserSession) -> None:
        now = time.time()
        user_session.sid_hash = new_hsid
        user_session.cts = now
        user_session.uts = now
        async with db.session() as data:
            if old_hsid is not None:
                statement = sqlmodel.select(sql.UserSession).where(sql.UserSession.sid_hash == old_hsid)
                result = await data.execute(statement)
                old_session = result.scalar_one_or_none()
                if old_session is not None:
                    await data.delete(old_session)
            data.add(user_session)
            await data.commit()

    async def revoke_by_uid(self, uid: str) -> int:
        async with db.session() as data:
            statement = sqlmodel.select(sql.UserSession).where(
                (sql.UserSession.uid == uid) | (sql.UserSession.admin_uid == uid)
            )
            result = await data.execute(statement)
            sessions = list(result.scalars().all())
            count = len(sessions)
            for user_session in sessions:
                await data.delete(user_session)
            if count > 0:
                await data.commit()
        return count

    async def save(self, user_session: sql.UserSession, fields: set[MutableSessionField]) -> None:
        if not fields.issubset(_MUTABLE_SESSION_FIELDS):
            raise ValueError("Invalid fields")
        async with db.session() as data:
            statement = sqlmodel.select(sql.UserSession).where(sql.UserSession.sid_hash == user_session.sid_hash)
            result = await data.execute(statement)
            existing = result.scalar_one_or_none()
            if existing is None:
                return
            for field in fields:
                setattr(existing, field, getattr(user_session, field))
            await data.commit()

    async def validate(self, hsid: str) -> sql.UserSession | None:
        try:
            cached = getattr(quart.g, "_user_session", None)
            if isinstance(cached, sql.UserSession) and cached.sid_hash == hsid:
                return cached
        except RuntimeError:
            pass
        max_session_age = config.get().MAX_SESSION_AGE
        async with db.session() as data:
            statement = sqlmodel.select(sql.UserSession).where(sql.UserSession.sid_hash == hsid)
            result = await data.execute(statement)
            user_session = result.scalar_one_or_none()
            if user_session is None:
                return None
            now = time.time()
            if user_session.uts < (now - _SESSION_IDLE_TIMEOUT):
                await data.delete(user_session)
                await data.commit()
                return None
            if (max_session_age > 0) and (user_session.cts < (now - max_session_age)):
                await data.delete(user_session)
                await data.commit()
                return None
            user_session.uts = now
            await data.commit()
            try:
                quart.g._user_session = user_session
            except RuntimeError:
                pass
            return user_session
