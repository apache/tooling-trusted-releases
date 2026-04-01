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

import pathlib
import unittest.mock as mock
from typing import TYPE_CHECKING

import pytest

import atr.cache as cache
import atr.ldap as ldap

if TYPE_CHECKING:
    from pytest import MonkeyPatch


class MockApp:
    def __init__(self):
        self.extensions: dict[str, object] = {}


class MockConfig:
    def __init__(
        self,
        state_dir: pathlib.Path | None = None,
        ldap_bind_dn: str | None = None,
        ldap_bind_password: str | None = None,
    ):
        self.STATE_DIR = str(state_dir) if state_dir else ""
        self.LDAP_BIND_DN = ldap_bind_dn
        self.LDAP_BIND_PASSWORD = ldap_bind_password


@pytest.fixture
def ldap_configured() -> bool:
    return ldap.get_bind_credentials() is not None


@pytest.mark.asyncio
async def test_admins_startup_load_fetches_real_admins(
    ldap_configured: bool, tmp_path: pathlib.Path, monkeypatch: "MonkeyPatch"
):
    _skip_if_unavailable(ldap_configured)

    import atr.config as config

    real_config = config.get()
    mock_config = MockConfig(tmp_path, real_config.LDAP_BIND_DN, real_config.LDAP_BIND_PASSWORD)
    monkeypatch.setattr("atr.config.get", lambda: mock_config)

    mock_app = MockApp()
    monkeypatch.setattr("asfquart.APP", mock_app)

    await cache.admins_startup_load()

    admins = mock_app.extensions.get("admins")
    assert admins is not None
    assert isinstance(admins, frozenset)
    assert len(admins) > 1
    assert "wave" in admins

    cache_path = tmp_path / "cache" / "admins.json"
    assert cache_path.exists()


@pytest.mark.asyncio
async def test_fetch_admin_users_contains_only_nonempty_strings(ldap_configured: bool):
    _skip_if_unavailable(ldap_configured)
    admins = await ldap.fetch_admin_users()
    assert all(isinstance(uid, str) and uid for uid in admins)


@pytest.mark.asyncio
async def test_fetch_admin_users_includes_wave(ldap_configured: bool):
    _skip_if_unavailable(ldap_configured)
    admins = await ldap.fetch_admin_users()
    assert "wave" in admins


@pytest.mark.asyncio
async def test_fetch_admin_users_is_idempotent(ldap_configured: bool):
    # Could, of course, fail in rare situations
    _skip_if_unavailable(ldap_configured)
    admins1 = await ldap.fetch_admin_users()
    admins2 = await ldap.fetch_admin_users()
    assert admins1 == admins2


@pytest.mark.asyncio
async def test_fetch_admin_users_returns_frozenset(ldap_configured: bool):
    _skip_if_unavailable(ldap_configured)
    admins = await ldap.fetch_admin_users()
    assert isinstance(admins, frozenset)


@pytest.mark.asyncio
async def test_fetch_admin_users_returns_reasonable_count(ldap_configured: bool):
    _skip_if_unavailable(ldap_configured)
    admins = await ldap.fetch_admin_users()
    assert len(admins) > 1
    assert len(admins) < 100


@pytest.mark.asyncio
async def test_is_active_returns_true_when_ldap_not_configured(monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: None)
    assert await ldap.is_active("anyone") is True


@pytest.mark.asyncio
async def test_is_active_returns_true_for_test_user_when_tests_allowed(monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: ("dn", "pw"))
    monkeypatch.setattr("atr.config.is_test_mode", lambda: True)
    assert await ldap.is_active("test") is True


@pytest.mark.asyncio
async def test_is_active_returns_false_for_test_banned_user_when_tests_allowed(monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: ("dn", "pw"))
    monkeypatch.setattr("atr.config.is_test_mode", lambda: True)
    assert await ldap.is_active("test-banned") is False


@pytest.mark.asyncio
async def test_is_active_returns_false_when_account_not_found(monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: ("dn", "pw"))
    monkeypatch.setattr("atr.config.is_test_mode", lambda: True)
    monkeypatch.setattr("atr.ldap.account_lookup", mock.AsyncMock(return_value=None))
    assert await ldap.is_active("ghost") is False


@pytest.mark.asyncio
async def test_is_active_returns_true_for_active_account(monkeypatch: "MonkeyPatch"):
    account = ldap.Result(dn="uid=alice,ou=people,dc=apache,dc=org", uid=["alice"])
    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: ("dn", "pw"))
    monkeypatch.setattr("atr.config.is_test_mode", lambda: True)
    monkeypatch.setattr("atr.ldap.account_lookup", mock.AsyncMock(return_value=account))
    assert await ldap.is_active("alice") is True


@pytest.mark.asyncio
async def test_is_active_returns_false_for_banned_account(monkeypatch: "MonkeyPatch"):
    account = ldap.Result.model_validate(
        {"dn": "uid=bad,ou=people,dc=apache,dc=org", "uid": ["bad"], "asf-banned": ["yes"]}
    )
    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: ("dn", "pw"))
    monkeypatch.setattr("atr.config.is_test_mode", lambda: True)
    monkeypatch.setattr("atr.ldap.account_lookup", mock.AsyncMock(return_value=account))
    assert await ldap.is_active("bad") is False


def test_is_banned_returns_false_for_account_without_flag():
    account = ldap.Result(dn="uid=alice,ou=people,dc=apache,dc=org", uid=["alice"])
    assert ldap.is_banned(account) is False


def test_is_banned_returns_true_for_account_with_flag():
    account = ldap.Result.model_validate(
        {"dn": "uid=bad,ou=people,dc=apache,dc=org", "uid": ["bad"], "asf-banned": ["yes"]}
    )
    assert ldap.is_banned(account) is True


def _skip_if_unavailable(ldap_configured: bool) -> None:
    if not ldap_configured:
        pytest.skip("LDAP not configured")
