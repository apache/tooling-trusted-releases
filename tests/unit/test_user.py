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

from typing import TYPE_CHECKING

import pytest

import atr.config as config
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
