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

import contextlib
import unittest.mock as mock
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest

import atr.config as config
import atr.models.sql as sql
import atr.user as user

if TYPE_CHECKING:
    from pytest import MonkeyPatch


class MockApp:
    def __init__(self):
        self.extensions: dict[str, object] = {}


class MockConfig:
    def __init__(self, admin_users_additional: str = ""):
        self.ADMIN_USERS_ADDITIONAL = admin_users_additional


@pytest.fixture
def mock_app(monkeypatch: "MonkeyPatch") -> MockApp:
    app = MockApp()
    monkeypatch.setattr("asfquart.APP", app)
    return app


@pytest.mark.asyncio
async def test_is_admin_async_returns_false_for_none(mock_app: MockApp, monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset()
    assert await user.is_admin_async(None) is False


@pytest.mark.asyncio
async def test_is_admin_async_returns_true_for_cached_admin(mock_app: MockApp, monkeypatch: "MonkeyPatch"):
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset({"async_admin"})
    assert await user.is_admin_async("async_admin") is True


@pytest.mark.asyncio
async def test_is_admin_async_returns_true_for_test_user(mock_app: MockApp, monkeypatch: "MonkeyPatch"):
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get_mode", lambda: config.Mode.Test)
    mock_app.extensions["admins"] = frozenset()
    assert await user.is_admin_async("test") is True


def test_is_admin_returns_false_for_none(mock_app: MockApp, monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset()
    assert user.is_admin(None) is False


def test_is_admin_returns_false_for_test_user_when_not_allowed(mock_app: MockApp, monkeypatch: "MonkeyPatch"):
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get_mode", lambda: config.Mode.Debug)
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset()
    assert user.is_admin("test") is False


def test_is_admin_returns_false_for_unknown_user(mock_app: MockApp, monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset({"alice", "bob"})
    assert user.is_admin("nobody") is False


def test_is_admin_returns_true_for_additional_admin(mock_app: MockApp, monkeypatch: "MonkeyPatch"):
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get", lambda: MockConfig(admin_users_additional="alice,bob"))
    mock_app.extensions["admins"] = frozenset()
    assert user.is_admin("alice") is True
    assert user.is_admin("bob") is True


def test_is_admin_returns_true_for_cached_admin(mock_app: MockApp, monkeypatch: "MonkeyPatch"):
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset({"cached_admin"})
    assert user.is_admin("cached_admin") is True


def test_is_admin_returns_true_for_test_user_when_allowed(mock_app: MockApp, monkeypatch: "MonkeyPatch"):
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get_mode", lambda: config.Mode.Test)
    mock_app.extensions["admins"] = frozenset()
    assert user.is_admin("test") is True


@pytest.mark.asyncio
async def test_is_binding_for_release_uses_explicit_voter_and_vote_round(monkeypatch: "MonkeyPatch"):
    committee = sql.Committee(key="example", name="Example", committee_members=["alice"])
    podling = sql.Committee(key="podling", name="Podling", is_podling=True, committee_members=["ppmc"])
    incubator = sql.Committee(key="incubator", name="Incubator", committee_members=["ipmc"])
    query = mock.MagicMock()
    query.get = mock.AsyncMock(return_value=incubator)
    data = mock.MagicMock()
    data.committee = mock.MagicMock(return_value=query)
    monkeypatch.setattr(user.db, "session", lambda: _mock_db_session(data))

    assert await user.is_binding_for_release(committee, "alice", None) == (True, "Example")
    assert await user.is_binding_for_release(committee, "bob", None) == (False, "Example")
    assert await user.is_binding_for_release(podling, "ppmc", 1) == (False, "Incubator")
    assert await user.is_binding_for_release(podling, "ipmc", 1) == (True, "Incubator")
    assert await user.is_binding_for_release(podling, "ipmc", 2) == (True, "Incubator")
    assert await user.is_binding_for_release(podling, "ppmc", 2) == (False, "Incubator")
    with pytest.raises(ValueError, match="Podling votes require vote_round 1 or 2"):
        await user.is_binding_for_release(podling, "ppmc", None)
    with pytest.raises(ValueError, match="Unexpected podling vote_round: 3"):
        await user.is_binding_for_release(podling, "ppmc", 3)
    with pytest.raises(ValueError, match="Non-podling votes require vote_round to be None"):
        await user.is_binding_for_release(committee, "alice", 1)

    assert data.committee.call_count == 4
    data.committee.assert_called_with(key="incubator")
    assert query.get.await_count == 4


@contextlib.asynccontextmanager
async def _mock_db_session(data: mock.MagicMock) -> AsyncIterator[mock.MagicMock]:
    yield data
