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

import types
import unittest.mock as mock

import pytest
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.sessions as sessions

_MOCK_CONFIG = types.SimpleNamespace(MAX_SESSION_AGE=0)

_COUNTER = 0


@pytest.fixture
async def store(monkeypatch):
    engine = sqlalchemy.ext.asyncio.create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)

    db._global_atr_sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(
        bind=engine, class_=db.Session, expire_on_commit=False
    )

    monkeypatch.setattr(sessions.config, "get", lambda: _MOCK_CONFIG)

    global _COUNTER
    _COUNTER = 0

    yield sessions.Store()

    await engine.dispose()
    db._global_atr_sessionmaker = None


async def test_create_maps_pmcs_to_committees(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    user_session = await store.validate(hsid)
    assert user_session is not None
    assert user_session.committees == ["test"]


async def test_create_maps_roleaccount_to_is_role(store):
    hsid = _hsid()
    await store.create(hsid, {"uid": "alice", "roleaccount": True, "pmcs": []})
    user_session = await store.validate(hsid)
    assert user_session is not None
    assert user_session.is_role is True


async def test_create_sets_timestamps(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    user_session = await store.validate(hsid)
    assert user_session is not None
    assert user_session.cts > 0
    assert user_session.uts > 0
    assert user_session.uts >= user_session.cts


async def test_create_validate_destroy_lifecycle(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())

    user_session = await store.validate(hsid)
    assert user_session is not None
    assert isinstance(user_session, sql.UserSession)
    assert user_session.uid == "alice"

    await store.destroy(hsid)
    assert await store.validate(hsid) is None


async def test_destroy_does_not_allow_reactivation(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    await store.destroy(hsid)
    assert await store.validate(hsid) is None

    new_hsid = _hsid()
    await store.create(new_hsid, _session_data())
    assert await store.validate(new_hsid) is not None
    assert await store.validate(hsid) is None


async def test_destroy_nonexistent_hsid(store):
    await store.destroy("nonexistent-hash")


async def test_destroy_then_save_does_not_resurrect(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    user_session = await store.validate(hsid)
    assert user_session is not None

    await store.destroy(hsid)
    await store.save(user_session, {"last_account_check"})
    assert await store.validate(hsid) is None


async def test_multiple_sessions_same_user(store):
    hsid1 = _hsid()
    hsid2 = _hsid()
    await store.create(hsid1, _session_data(uid="alice"))
    await store.create(hsid2, _session_data(uid="alice"))

    s1 = await store.validate(hsid1)
    s2 = await store.validate(hsid2)
    assert s1 is not None
    assert s2 is not None
    assert s1.sid_hash != s2.sid_hash

    await store.destroy(hsid1)
    assert await store.validate(hsid1) is None
    assert await store.validate(hsid2) is not None


async def test_revoke_by_uid_impersonation_by_target(store):
    hsid = _hsid()
    await store.create(hsid, _session_data(uid="target", admin_uid="admin_user"))

    count = await store.revoke_by_uid("target")
    assert count == 1
    assert await store.validate(hsid) is None
    assert await store.revoke_by_uid("admin_user") == 0


async def test_revoke_by_uid_impersonation_session(store):
    hsid = _hsid()
    await store.create(hsid, _session_data(uid="target", admin_uid="admin_user"))

    count = await store.revoke_by_uid("admin_user")
    assert count == 1
    assert await store.validate(hsid) is None


async def test_revoke_by_uid_nonexistent(store):
    assert await store.revoke_by_uid("nobody") == 0


async def test_revoke_by_uid_removes_all_sessions(store):
    hsid1 = _hsid()
    hsid2 = _hsid()
    hsid3 = _hsid()
    await store.create(hsid1, _session_data(uid="bob"))
    await store.create(hsid2, _session_data(uid="bob"))
    await store.create(hsid3, _session_data(uid="carol"))

    count = await store.revoke_by_uid("bob")
    assert count == 2
    assert await store.validate(hsid1) is None
    assert await store.validate(hsid2) is None
    assert await store.validate(hsid3) is not None


async def test_save_persists_mutations(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    user_session = await store.validate(hsid)
    assert user_session is not None

    user_session.last_account_check = 12345.0
    await store.save(user_session, {"last_account_check"})

    reloaded = await store.validate(hsid)
    assert reloaded is not None
    assert reloaded.last_account_check == 12345.0


async def test_user_session_email_default():
    user_session = sql.UserSession(uid="testuser")
    assert user_session.email == "testuser@apache.org"


async def test_user_session_email_explicit():
    user_session = sql.UserSession(uid="testuser", email="custom@example.org")
    assert user_session.email == "custom@example.org"


async def test_validate_nonexistent_hsid(store):
    assert await store.validate("nonexistent-hash") is None


async def test_validate_returns_user_session_instance(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    result = await store.validate(hsid)
    assert isinstance(result, sql.UserSession)


async def test_validate_updates_uts(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    first = await store.validate(hsid)
    assert first is not None
    first_uts = first.uts

    second = await store.validate(hsid)
    assert second is not None
    assert second.uts >= first_uts


@pytest.mark.asyncio
async def test_terminate_current_users_sessions_revokes_by_uid_clears_cookie_and_cache():
    mock_store = mock.MagicMock()
    mock_store.revoke_by_uid = mock.AsyncMock(return_value=3)
    mock_app = mock.MagicMock()
    mock_app.sessions = mock_store

    with (
        mock.patch.object(sessions.asfquart, "APP", mock_app),
        mock.patch.object(sessions.asfquart.session, "aclear", new=mock.AsyncMock()) as mock_aclear,
        mock.patch.object(sessions, "invalidate_cache") as mock_invalidate,
    ):
        count = await sessions.terminate_current_users_sessions("alice")

    assert count == 3
    mock_store.revoke_by_uid.assert_awaited_once_with("alice")
    mock_aclear.assert_awaited_once_with()
    mock_invalidate.assert_called_once_with()


@pytest.mark.asyncio
async def test_terminate_current_users_sessions_returns_zero_when_no_sessions():
    mock_store = mock.MagicMock()
    mock_store.revoke_by_uid = mock.AsyncMock(return_value=0)
    mock_app = mock.MagicMock()
    mock_app.sessions = mock_store

    with (
        mock.patch.object(sessions.asfquart, "APP", mock_app),
        mock.patch.object(sessions.asfquart.session, "aclear", new=mock.AsyncMock()),
        mock.patch.object(sessions, "invalidate_cache"),
    ):
        count = await sessions.terminate_current_users_sessions("bob")

    assert count == 0


def _hsid() -> str:
    global _COUNTER
    _COUNTER += 1
    return f"test-hash-{_COUNTER}"


def _session_data(uid: str = "alice", **kwargs) -> dict:
    base = {"uid": uid, "pmcs": ["test"], "projects": ["test"]}
    base.update(kwargs)
    return base
