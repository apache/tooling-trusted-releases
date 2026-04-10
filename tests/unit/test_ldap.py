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


def _make_pubsub_payload(
    uid: str = "testuser",
    old_banned: list[str] | None = None,
    new_banned: list[str] | None = None,
) -> dict:
    """Build a minimal LDAP pubsub payload for testing."""
    return {
        "dn": f"uid={uid},ou=people,dc=apache,dc=org",
        "change_type": "modify",
        "old_attributes": {
            "uid": [uid],
            "asf-banned": old_banned if old_banned is not None else [],
        },
        "new_attributes": {
            "uid": [uid],
            "asf-banned": new_banned if new_banned is not None else [],
        },
        "pubsub_timestamp": 1756736128.0,
        "pubsub_topics": ["ldap"],
        "pubsub_path": "/private/ldap",
        "pubsub_cursor": "test-cursor",
    }


def _skip_if_unavailable(ldap_configured: bool) -> None:
    if not ldap_configured:
        pytest.skip("LDAP not configured")


# --- PubSub payload parsing tests ---


def test_pubsub_payload_parses_valid_event():
    payload = _make_pubsub_payload(uid="alice")
    parsed = ldap.PubSubPayload.model_validate(payload)
    assert parsed.dn == "uid=alice,ou=people,dc=apache,dc=org"
    assert parsed.change_type == "modify"
    assert parsed.new_attributes.uid == ["alice"]
    assert parsed.old_attributes.asf_banned == []


def test_pubsub_payload_ignores_extra_attributes():
    payload = _make_pubsub_payload()
    payload["new_attributes"]["mail"] = ["test@example.com"]
    payload["new_attributes"]["objectClass"] = ["person", "top"]
    parsed = ldap.PubSubPayload.model_validate(payload)
    assert parsed.new_attributes.uid == ["testuser"]


def test_pubsub_payload_detects_ban():
    payload = _make_pubsub_payload(old_banned=[], new_banned=["yes"])
    parsed = ldap.PubSubPayload.model_validate(payload)
    assert not bool(parsed.old_attributes.asf_banned)
    assert bool(parsed.new_attributes.asf_banned)


def test_pubsub_payload_detects_unban():
    payload = _make_pubsub_payload(old_banned=["yes"], new_banned=[])
    parsed = ldap.PubSubPayload.model_validate(payload)
    assert bool(parsed.old_attributes.asf_banned)
    assert not bool(parsed.new_attributes.asf_banned)


def test_pubsub_payload_no_change_when_both_unbanned():
    payload = _make_pubsub_payload(old_banned=[], new_banned=[])
    parsed = ldap.PubSubPayload.model_validate(payload)
    assert not bool(parsed.old_attributes.asf_banned)
    assert not bool(parsed.new_attributes.asf_banned)


def test_extract_uid_from_pubsub_prefers_attributes():
    payload = ldap.PubSubPayload.model_validate(_make_pubsub_payload(uid="alice"))
    assert ldap._extract_uid_from_pubsub(payload) == "alice"


def test_extract_uid_from_pubsub_falls_back_to_dn():
    payload = ldap.PubSubPayload.model_validate(
        {
            "dn": "uid=bob,ou=people,dc=apache,dc=org",
            "change_type": "modify",
            "old_attributes": {},
            "new_attributes": {},
        }
    )
    assert ldap._extract_uid_from_pubsub(payload) == "bob"


def test_extract_uid_from_pubsub_returns_none_without_uid():
    payload = ldap.PubSubPayload.model_validate(
        {
            "dn": "cn=some-group,ou=groups,dc=apache,dc=org",
            "change_type": "modify",
            "old_attributes": {},
            "new_attributes": {},
        }
    )
    assert ldap._extract_uid_from_pubsub(payload) is None


@pytest.mark.asyncio
async def test_handle_update_logs_deactivation(monkeypatch: "MonkeyPatch"):
    logged: list[str] = []
    monkeypatch.setattr("atr.log.info", lambda msg: logged.append(msg))
    monkeypatch.setattr("atr.log.debug", lambda msg: None)

    payload = _make_pubsub_payload(uid="baduser", old_banned=[], new_banned=["yes"])
    await ldap.handle_update(payload)

    assert any("baduser" in msg and "deactivated" in msg for msg in logged)


@pytest.mark.asyncio
async def test_handle_update_logs_reactivation(monkeypatch: "MonkeyPatch"):
    logged: list[str] = []
    monkeypatch.setattr("atr.log.info", lambda msg: logged.append(msg))
    monkeypatch.setattr("atr.log.debug", lambda msg: None)

    payload = _make_pubsub_payload(uid="gooduser", old_banned=["yes"], new_banned=[])
    await ldap.handle_update(payload)

    assert any("gooduser" in msg and "reactivated" in msg for msg in logged)


@pytest.mark.asyncio
async def test_handle_update_ignores_no_ban_change(monkeypatch: "MonkeyPatch"):
    logged: list[str] = []
    monkeypatch.setattr("atr.log.info", lambda msg: logged.append(msg))
    monkeypatch.setattr("atr.log.debug", lambda msg: None)

    payload = _make_pubsub_payload(uid="normaluser", old_banned=[], new_banned=[])
    await ldap.handle_update(payload)

    assert not logged


@pytest.mark.asyncio
async def test_handle_update_handles_invalid_payload(monkeypatch: "MonkeyPatch"):
    warnings: list[str] = []
    monkeypatch.setattr("atr.log.warning", lambda msg: warnings.append(msg))

    await ldap.handle_update({"not": "valid"})

    assert any("Failed to parse" in msg for msg in warnings)
