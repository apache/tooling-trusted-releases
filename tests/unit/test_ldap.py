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

import ldap3.core.exceptions
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


class MockRaisingConnection:
    def search(self, **kwargs: object) -> bool:
        raise ldap3.core.exceptions.LDAPSocketReceiveError("connection reset")


class MockResultConnection:
    def __init__(self, result_code: int):
        self.result = {"result": result_code}
        self.entries: list[object] = []

    def search(self, search_base: str, search_filter: str, attributes: list[str]) -> bool:
        return self.result["result"] == 0


@pytest.fixture
def ldap_configured() -> bool:
    return ldap.get_bind_credentials() is not None


@pytest.mark.asyncio
async def test_account_lookup_raises_when_ldap_unavailable(monkeypatch: "MonkeyPatch"):
    def failed_search(params: ldap.SearchParameters) -> None:
        params.failed = True
        params.err_msg = "An unexpected error occurred: connection refused"

    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: ("dn", "pw"))
    monkeypatch.setattr("atr.ldap.search", failed_search)
    with pytest.raises(ldap.UnavailableError):
        await ldap.account_lookup("alice")


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


def test_extract_uid_from_pubsub_prefers_attributes():
    payload = ldap.PubSubPayload.model_validate(_make_pubsub_payload(uid="alice"))
    assert ldap._extract_uid_from_pubsub(payload) == "alice"


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
async def test_github_to_apache_raises_when_ldap_unavailable(monkeypatch: "MonkeyPatch"):
    def failed_search(params: ldap.SearchParameters) -> None:
        params.failed = True
        params.err_msg = "An unexpected error occurred: connection refused"

    monkeypatch.setattr("atr.ldap.search", failed_search)
    with pytest.raises(ldap.UnavailableError):
        await ldap.github_to_apache(12345)


@pytest.mark.asyncio
async def test_handle_update_handles_invalid_payload(monkeypatch: "MonkeyPatch"):
    warnings: list[str] = []
    monkeypatch.setattr("atr.log.warning", lambda msg: warnings.append(msg))

    await ldap.handle_update({"not": "valid"})

    assert any("Failed to parse" in msg for msg in warnings)


@pytest.mark.asyncio
async def test_handle_update_ignores_no_ban_change(monkeypatch: "MonkeyPatch"):
    logged: list[str] = []
    auth_events: list[str] = []
    monkeypatch.setattr("atr.log.info", lambda msg: logged.append(msg))
    monkeypatch.setattr("atr.log.debug", lambda msg: None)
    monkeypatch.setattr("atr.log.auth_event", lambda event, asfuid=None, **kw: auth_events.append(event))

    payload = _make_pubsub_payload(uid="normaluser", old_banned=[], new_banned=[])
    await ldap.handle_update(payload)

    assert not logged
    assert not auth_events


@pytest.mark.asyncio
async def test_handle_update_logs_reactivation(monkeypatch: "MonkeyPatch"):
    logged: list[str] = []
    auth_events: list[str] = []
    monkeypatch.setattr("atr.log.info", lambda msg: logged.append(msg))
    monkeypatch.setattr("atr.log.debug", lambda msg: None)
    monkeypatch.setattr("atr.log.auth_event", lambda event, asfuid=None, **kw: auth_events.append(event))

    payload = _make_pubsub_payload(uid="gooduser", old_banned=["yes"], new_banned=[])
    await ldap.handle_update(payload)

    assert any("gooduser" in msg and "reactivated" in msg for msg in logged)
    assert "account_reactivated" in auth_events


@pytest.mark.asyncio
async def test_handle_update_revokes_all_credentials(monkeypatch: "MonkeyPatch"):
    logged, auth_events = _setup_ban_mocks(monkeypatch, session_count=2, token_count=3, ssh_key_count=1)

    payload = _make_pubsub_payload(uid="baduser", old_banned=[], new_banned=["yes"])
    await ldap.handle_update(payload)

    assert any("baduser" in msg and "deactivated" in msg for msg in logged)
    assert "account_deactivated" in auth_events
    assert "sessions_revoked" in auth_events
    assert "tokens_revoked" in auth_events
    assert "ssh_keys_revoked" in auth_events


@pytest.mark.asyncio
async def test_handle_update_revokes_only_sessions_when_no_keys_or_tokens(monkeypatch: "MonkeyPatch"):
    _logged, auth_events = _setup_ban_mocks(monkeypatch, session_count=1, token_count=0, ssh_key_count=0)

    payload = _make_pubsub_payload(uid="user1", old_banned=[], new_banned=["yes"])
    await ldap.handle_update(payload)

    assert "sessions_revoked" in auth_events
    assert "tokens_revoked" not in auth_events
    assert "ssh_keys_revoked" not in auth_events


@pytest.mark.asyncio
async def test_handle_update_skips_revoke_logs_when_nothing_to_revoke(monkeypatch: "MonkeyPatch"):
    _logged, auth_events = _setup_ban_mocks(monkeypatch, session_count=0, token_count=0, ssh_key_count=0)

    payload = _make_pubsub_payload(uid="nouser", old_banned=[], new_banned=["yes"])
    await ldap.handle_update(payload)

    assert "account_deactivated" in auth_events
    assert "sessions_revoked" not in auth_events
    assert "tokens_revoked" not in auth_events
    assert "ssh_keys_revoked" not in auth_events


@pytest.mark.asyncio
async def test_is_active_raises_when_ldap_unavailable(monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: ("dn", "pw"))
    monkeypatch.setattr("atr.config.is_test_mode", lambda: True)
    monkeypatch.setattr("atr.ldap.account_lookup", mock.AsyncMock(side_effect=ldap.UnavailableError("down")))
    with pytest.raises(ldap.UnavailableError):
        await ldap.is_active("alice")


@pytest.mark.asyncio
async def test_is_active_returns_false_for_banned_account(monkeypatch: "MonkeyPatch"):
    account = ldap.Result.model_validate(
        {"dn": "uid=bad,ou=people,dc=apache,dc=org", "uid": ["bad"], "asf-banned": ["yes"]}
    )
    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: ("dn", "pw"))
    monkeypatch.setattr("atr.config.is_test_mode", lambda: True)
    monkeypatch.setattr("atr.ldap.account_lookup", mock.AsyncMock(return_value=account))
    assert await ldap.is_active("bad") is False


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
async def test_is_active_returns_true_for_test_user_when_tests_allowed(monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: ("dn", "pw"))
    monkeypatch.setattr("atr.config.is_test_mode", lambda: True)
    assert await ldap.is_active("test") is True


@pytest.mark.asyncio
async def test_is_active_returns_true_when_ldap_not_configured(monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.ldap.get_bind_credentials", lambda: None)
    assert await ldap.is_active("anyone") is True


def test_is_banned_returns_false_for_account_without_flag():
    account = ldap.Result(dn="uid=alice,ou=people,dc=apache,dc=org", uid=["alice"])
    assert ldap.is_banned(account) is False


def test_is_banned_returns_true_for_account_with_flag():
    account = ldap.Result.model_validate(
        {"dn": "uid=bad,ou=people,dc=apache,dc=org", "uid": ["bad"], "asf-banned": ["yes"]}
    )
    assert ldap.is_banned(account) is True


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


def test_pubsub_payload_ignores_extra_attributes():
    payload = _make_pubsub_payload()
    payload["new_attributes"]["mail"] = ["test@example.com"]
    payload["new_attributes"]["objectClass"] = ["person", "top"]
    parsed = ldap.PubSubPayload.model_validate(payload)
    assert parsed.new_attributes.uid == ["testuser"]


def test_pubsub_payload_no_change_when_both_unbanned():
    payload = _make_pubsub_payload(old_banned=[], new_banned=[])
    parsed = ldap.PubSubPayload.model_validate(payload)
    assert not bool(parsed.old_attributes.asf_banned)
    assert not bool(parsed.new_attributes.asf_banned)


def test_pubsub_payload_parses_valid_event():
    payload = _make_pubsub_payload(uid="alice")
    parsed = ldap.PubSubPayload.model_validate(payload)
    assert parsed.dn == "uid=alice,ou=people,dc=apache,dc=org"
    assert parsed.change_type == "modify"
    assert parsed.new_attributes.uid == ["alice"]
    assert parsed.old_attributes.asf_banned == []


def test_search_class_wraps_transport_errors():
    ldap_search = ldap.Search("dn", "pw")
    ldap_search._conn = MockRaisingConnection()
    with pytest.raises(ldap.UnavailableError):
        ldap_search.search(ldap_base="ou=people,dc=apache,dc=org", ldap_scope="BASE")


def test_search_core_2_reports_no_results_on_empty_success():
    params = ldap.SearchParameters(uid_query="alice")
    params.connection = MockResultConnection(0)
    ldap._search_core_2(params, ["(uid=alice)"])
    assert params.failed is False
    assert params.err_msg == "No results found for the given criteria."


def test_search_core_2_sets_failed_on_error_result():
    params = ldap.SearchParameters(uid_query="alice")
    params.connection = MockResultConnection(52)
    ldap._search_core_2(params, ["(uid=alice)"])
    assert params.failed is True


def test_search_core_2_sets_failed_on_no_such_object():
    params = ldap.SearchParameters(uid_query="alice")
    params.connection = MockResultConnection(32)
    ldap._search_core_2(params, ["(uid=alice)"])
    assert params.failed is True


def test_search_sets_failed_on_exception(monkeypatch: "MonkeyPatch"):
    def broken_search_core(params: ldap.SearchParameters) -> None:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr("atr.ldap._search_core", broken_search_core)
    params = ldap.SearchParameters(uid_query="alice")
    ldap.search(params)
    assert params.failed is True
    assert params.err_msg is not None


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


def _mock_db_session(token_items: list | None = None, ssh_key_items: list | None = None) -> mock.MagicMock:
    """Build a mock db.session() context manager following the test_quarantine_task pattern."""
    mock_data = mock.AsyncMock()
    mock_data.delete = mock.AsyncMock()
    mock_data.commit = mock.AsyncMock()

    # execute() is called twice: first for tokens, then for SSH keys
    # Each result needs .scalars().all() to return the items
    pat_query = mock.MagicMock()
    pat_query.all = mock.AsyncMock(return_value=token_items or [])
    mock_data.personal_access_token = mock.MagicMock(return_value=pat_query)

    # execute() is now called only for SSH keys.
    ssh_result = mock.MagicMock()
    ssh_result.scalars.return_value.all.return_value = ssh_key_items or []
    mock_data.execute = mock.AsyncMock(return_value=ssh_result)

    mock_session_ctx = mock.AsyncMock()
    mock_session_ctx.__aenter__ = mock.AsyncMock(return_value=mock_data)
    mock_session_ctx.__aexit__ = mock.AsyncMock(return_value=False)
    return mock_session_ctx


def _setup_ban_mocks(
    monkeypatch: "MonkeyPatch",
    session_count: int = 0,
    token_count: int = 0,
    ssh_key_count: int = 0,
) -> tuple[list[str], list[str]]:
    """Set up common mocks for ban/deactivation tests. Returns (logged, auth_events)."""
    logged: list[str] = []
    auth_events: list[str] = []
    monkeypatch.setattr("atr.log.info", lambda msg: logged.append(msg))
    monkeypatch.setattr("atr.log.debug", lambda msg: None)
    monkeypatch.setattr("atr.log.auth_event", lambda event, asfuid=None, **kw: auth_events.append(event))

    mock_sessions = mock.MagicMock()
    mock_sessions.revoke_by_uid = mock.AsyncMock(return_value=session_count)
    mock_app = mock.MagicMock()
    mock_app.sessions = mock_sessions
    monkeypatch.setattr("asfquart.APP", mock_app)

    mock_db = _mock_db_session(
        token_items=[mock.MagicMock() for _ in range(token_count)],
        ssh_key_items=[mock.MagicMock() for _ in range(ssh_key_count)],
    )
    monkeypatch.setattr("atr.db.session", lambda: mock_db)

    return logged, auth_events


def _skip_if_unavailable(ldap_configured: bool) -> None:
    if not ldap_configured:
        pytest.skip("LDAP not configured")
