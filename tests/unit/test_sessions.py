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

import hashlib
import time
import types
import unittest.mock as mock

import asfquart.base as base
import pytest
import quart
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.get as get
import atr.ldap as ldap
import atr.models.sql as sql
import atr.post as post
import atr.sessions as sessions

_MOCK_CONFIG = types.SimpleNamespace(ADMIN_ONLY=False, MAX_SESSION_AGE=0)

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

    mock_request = mock.MagicMock()
    mock_request.remote_addr = "127.0.0.1"
    monkeypatch.setattr("quart.request", mock_request)

    global _COUNTER
    _COUNTER = 0

    yield sessions.Store()

    await engine.dispose()
    db._global_atr_sessionmaker = None


async def test_account_check_fails_open_when_ldap_unavailable(monkeypatch):
    user_session = sql.UserSession(uid="alice")
    monkeypatch.setattr("atr.ldap.is_active", mock.AsyncMock(side_effect=ldap.UnavailableError("down")))
    deleted = mock.AsyncMock()
    monkeypatch.setattr("atr.sessions.deleted_or_banned", deleted)
    save = mock.AsyncMock()
    monkeypatch.setattr("asfquart.APP", types.SimpleNamespace(sessions=types.SimpleNamespace(save=save)))

    await sessions.account_check(user_session, 123.0)

    deleted.assert_not_awaited()
    save.assert_awaited_once_with(user_session, {"last_account_check": 123.0})


async def test_account_check_revokes_user_when_admin_check_unavailable(monkeypatch):
    user_session = sql.UserSession(uid="alice", admin_uid="admin")

    async def is_active(uid: str) -> bool:
        if uid == "admin":
            raise ldap.UnavailableError("down")
        return False

    monkeypatch.setattr("atr.ldap.is_active", is_active)
    deleted = mock.AsyncMock()
    monkeypatch.setattr("atr.sessions.deleted_or_banned", deleted)

    with pytest.raises(base.ASFQuartException):
        await sessions.account_check(user_session, 123.0)

    deleted.assert_awaited_once_with("alice")


async def test_account_check_saves_timestamp_when_active(monkeypatch):
    user_session = sql.UserSession(uid="alice")
    monkeypatch.setattr("atr.ldap.is_active", mock.AsyncMock(return_value=True))
    save = mock.AsyncMock()
    monkeypatch.setattr("asfquart.APP", types.SimpleNamespace(sessions=types.SimpleNamespace(save=save)))

    await sessions.account_check(user_session, 123.0)

    save.assert_awaited_once_with(user_session, {"last_account_check": 123.0})


async def test_create_logs_auth_success(store, monkeypatch):
    events: list[tuple[str, str | None]] = []
    monkeypatch.setattr("atr.log.auth_success", lambda type, asfuid=None: events.append((type, asfuid)))

    await store.create(_hsid(), {"uid": "alice"})

    assert events == [("oauth", "alice")]


async def test_create_maps_pmcs_to_member_committees(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    user_session = await store.validate(hsid)
    assert user_session is not None
    assert user_session.member_committees == ["test"]


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


async def test_current_and_validate_expire_sessions_idle_for_three_days(store):
    now = time.time()
    created = now - 4 * 86400
    cutoff = now - 3 * 86400
    async with db.session() as data:
        data.add(sql.UserSession(sid_hash="idle", uid="alice", cts=created, uts=cutoff - 60))
        data.add(sql.UserSession(sid_hash="active", uid="alice", cts=created, uts=cutoff + 60))
        await data.commit()

    assert [session.sid_hash for session in await store.current()] == ["active"]
    assert await store.validate("idle") is None
    assert await store.validate("active") is not None


async def test_destroy_does_not_allow_reactivation(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    await store.destroy(hsid)
    assert await store.validate(hsid) is None

    new_hsid = _hsid()
    await store.create(new_hsid, _session_data())
    assert await store.validate(new_hsid) is not None
    assert await store.validate(hsid) is None


async def test_destroy_logs_logout(store, monkeypatch):
    events: list[tuple[str, str | None]] = []
    monkeypatch.setattr("atr.log.auth_event", lambda event, asfuid=None: events.append((event, asfuid)))
    hsid = _hsid()
    await store.create(hsid, _session_data())

    await store.destroy(hsid)
    await store.destroy(hsid)

    assert events == [("logout", "alice")]


async def test_destroy_nonexistent_hsid(store):
    await store.destroy("nonexistent-hash")


async def test_destroy_then_save_does_not_resurrect(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    user_session = await store.validate(hsid)
    assert user_session is not None

    await store.destroy(hsid)
    await store.save(user_session, {"last_account_check": 12345.0})
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


@pytest.mark.parametrize(
    ("uid", "admin_uid", "count"),
    [("alice", None, 1), ("target", "alice", 1), ("bob", None, 0)],
)
async def test_revoke_by_uid_hsid_removes_only_that_owned_session(store, uid, admin_uid, count):
    hsid = _hsid()
    other_hsid = _hsid()
    await store.create(hsid, _session_data(uid=uid, admin_uid=admin_uid))
    await store.create(other_hsid, _session_data(uid="alice"))

    assert await store.revoke_by_uid("alice", "missing") == 0
    assert await store.revoke_by_uid("alice", hsid) == count
    assert (await store.validate(hsid) is None) == (count == 1)
    assert await store.validate(other_hsid) is not None


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


async def test_save_persists_values(store):
    hsid = _hsid()
    await store.create(hsid, _session_data())
    user_session = await store.validate(hsid)
    assert user_session is not None

    await store.save(user_session, {"last_account_check": 12345.0})
    assert user_session.last_account_check == 12345.0

    reloaded = await store.validate(hsid)
    assert reloaded is not None
    assert reloaded.last_account_check == 12345.0


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


@pytest.mark.parametrize(
    ("target", "location", "remaining", "signed_in"),
    [
        (None, "/", {"bob"}, False),
        ("current", "/", {"other", "bob"}, False),
        ("other", "/user/sessions", {"current", "bob"}, True),
        ("bob", "/user/sessions", {"current", "other", "bob"}, True),
    ],
)
async def test_terminate_sessions_route(store, monkeypatch, target, location, remaining, signed_in):
    hashes = {"current": hashlib.sha3_256(b"current-sid").hexdigest(), "other": "other", "bob": "bob"}
    async with db.session() as data:
        for name, hsid in hashes.items():
            data.add(sql.UserSession(sid_hash=hsid, uid="bob" if (name == "bob") else "alice", uts=time.time()))
        await data.commit()
    app = quart.Quart(__name__)
    app.secret_key = "test-key"
    app.app_id = "atr"
    app.sessions = store
    monkeypatch.setattr("asfquart.APP", app)
    monkeypatch.setattr("quart.request", quart.globals.request)
    app.add_url_rule("/", endpoint=get.root.index.endpoint, view_func=get.root.index)
    app.add_url_rule("/user/sessions", endpoint=get.user.sessions.endpoint, view_func=get.user.sessions)
    app.add_url_rule(
        "/user/sessions",
        endpoint=post.user.terminate_sessions.endpoint,
        view_func=post.user.terminate_sessions,
        methods=["POST"],
    )
    client = app.test_client()
    async with client.session_transaction() as cookie:
        cookie["atr"] = {"sid": "current-sid"}

    form = {"variant": "terminate_all", "csrf_token": "x"}
    if target is not None:
        form = {"variant": "terminate_one", "sid_hash": hashes[target], "csrf_token": "x"}
    response = await client.post("/user/sessions", form=form)

    assert response.status_code == 302
    assert response.headers["Location"] == location
    names = {hsid: name for name, hsid in hashes.items()}
    async with db.session() as data:
        assert {names[s.sid_hash] for s in await data.usersession().all()} == remaining
    async with client.session_transaction() as cookie:
        assert ("atr" in cookie) is signed_in


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


def _hsid() -> str:
    global _COUNTER
    _COUNTER += 1
    return f"test-hash-{_COUNTER}"


def _session_data(uid: str = "alice", **kwargs) -> dict:
    base = {"uid": uid, "pmcs": ["test"], "projects": ["test"]}
    base.update(kwargs)
    return base
