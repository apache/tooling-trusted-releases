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

import datetime
import json
import pathlib
from typing import TYPE_CHECKING

import pydantic
import pytest

import atr.cache as cache

if TYPE_CHECKING:
    from pytest import MonkeyPatch


class MockApp:
    def __init__(self):
        self.extensions: dict[str, object] = {}


class MockConfig:
    def __init__(self, state_dir: pathlib.Path):
        self.STATE_DIR = str(state_dir)


@pytest.fixture
def mock_app(monkeypatch: "MonkeyPatch") -> MockApp:
    app = MockApp()
    monkeypatch.setattr("asfquart.APP", app)
    return app


@pytest.fixture
def state_dir(tmp_path: pathlib.Path, monkeypatch: "MonkeyPatch") -> pathlib.Path:
    monkeypatch.setattr("atr.config.get", lambda: MockConfig(tmp_path))
    return tmp_path


def test_admins_cache_rejects_missing_admins():
    with pytest.raises(pydantic.ValidationError):
        cache.AdminsCache.model_validate({"refreshed": "2025-01-01T00:00:00Z"})


def test_admins_cache_rejects_missing_refreshed():
    with pytest.raises(pydantic.ValidationError):
        cache.AdminsCache.model_validate({"admins": ["alice"]})


def test_admins_cache_roundtrip_json():
    original = cache.AdminsCache(
        refreshed=datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.UTC),
        admins=frozenset({"alice", "bob", "charlie"}),
    )
    json_str = original.model_dump_json()
    restored = cache.AdminsCache.model_validate_json(json_str)
    assert restored.refreshed == original.refreshed
    assert restored.admins == original.admins


def test_admins_cache_serializes_to_json():
    data = cache.AdminsCache(
        refreshed=datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.UTC),
        admins=frozenset({"alice", "bob"}),
    )
    json_str = data.model_dump_json()
    parsed = json.loads(json_str)
    assert "refreshed" in parsed
    assert "admins" in parsed
    assert set(parsed["admins"]) == {"alice", "bob"}


def test_admins_cache_validates_with_good_data():
    data = cache.AdminsCache(
        refreshed=datetime.datetime.now(datetime.UTC),
        admins=frozenset({"alice", "bob"}),
    )
    assert isinstance(data.refreshed, datetime.datetime)
    assert data.admins == frozenset({"alice", "bob"})


@pytest.mark.asyncio
async def test_admins_get_async_falls_back_to_file_without_app_context(
    state_dir: pathlib.Path, monkeypatch: "MonkeyPatch"
):
    def raise_runtime_error() -> frozenset[str]:
        raise RuntimeError("No app context")

    monkeypatch.setattr("atr.cache.admins_get", raise_runtime_error)

    await cache.admins_save_to_file(frozenset({"file_alice", "file_bob"}))
    result = await cache.admins_get_async()
    assert result == frozenset({"file_alice", "file_bob"})


@pytest.mark.asyncio
async def test_admins_get_async_uses_extensions_when_available(mock_app: MockApp):
    mock_app.extensions["admins"] = frozenset({"async_alice"})
    result = await cache.admins_get_async()
    assert result == frozenset({"async_alice"})


def test_admins_get_returns_empty_frozenset_when_not_set(mock_app: MockApp):
    result = cache.admins_get()
    assert result == frozenset()


def test_admins_get_returns_frozenset_from_extensions(mock_app: MockApp):
    mock_app.extensions["admins"] = frozenset({"alice", "bob"})
    result = cache.admins_get()
    assert result == frozenset({"alice", "bob"})


@pytest.mark.asyncio
async def test_admins_read_from_file_returns_none_for_invalid_json(state_dir: pathlib.Path):
    cache_path = state_dir / "cache" / "admins.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text("not valid json {{{")
    result = await cache._admins_read_from_file_async()
    assert result is None


@pytest.mark.asyncio
async def test_admins_read_from_file_returns_none_for_invalid_schema(state_dir: pathlib.Path):
    cache_path = state_dir / "cache" / "admins.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text('{"wrong_field": "value"}')
    result = await cache._admins_read_from_file_async()
    assert result is None


@pytest.mark.asyncio
async def test_admins_read_from_file_returns_none_for_missing(state_dir: pathlib.Path):
    result = await cache._admins_read_from_file_async()
    assert result is None


@pytest.mark.asyncio
async def test_admins_save_and_read_roundtrip(state_dir: pathlib.Path):
    original_admins = frozenset({"alice", "bob", "charlie"})
    await cache.admins_save_to_file(original_admins)
    result = await cache._admins_read_from_file_async()
    assert result is not None
    assert result.admins == original_admins


@pytest.mark.asyncio
async def test_admins_save_to_file_creates_file(state_dir: pathlib.Path):
    admins = frozenset({"alice", "bob"})
    await cache.admins_save_to_file(admins)
    cache_path = state_dir / "cache" / "admins.json"
    assert cache_path.exists()


@pytest.mark.asyncio
async def test_admins_save_to_file_creates_parent_dirs(state_dir: pathlib.Path):
    cache_dir = state_dir / "cache"
    assert not cache_dir.exists()
    await cache.admins_save_to_file(frozenset({"alice"}))
    assert cache_dir.exists()
    assert cache_dir.is_dir()


@pytest.mark.asyncio
async def test_admins_startup_load_calls_ldap_when_cache_missing(
    state_dir: pathlib.Path, mock_app: MockApp, monkeypatch: "MonkeyPatch"
):
    ldap_called = False

    async def mock_fetch_admin_users() -> frozenset[str]:
        nonlocal ldap_called
        ldap_called = True
        return frozenset({"ldap_alice", "ldap_bob"})

    monkeypatch.setattr("atr.ldap.fetch_admin_users", mock_fetch_admin_users)

    await cache.admins_startup_load()

    assert ldap_called is True
    assert mock_app.extensions["admins"] == frozenset({"ldap_alice", "ldap_bob"})
    cache_path = state_dir / "cache" / "admins.json"
    assert cache_path.exists()


@pytest.mark.asyncio
async def test_admins_startup_load_handles_ldap_failure(
    state_dir: pathlib.Path, mock_app: MockApp, monkeypatch: "MonkeyPatch"
):
    async def mock_fetch_admin_users() -> frozenset[str]:
        raise ConnectionError("LDAP server unavailable")

    monkeypatch.setattr("atr.ldap.fetch_admin_users", mock_fetch_admin_users)

    await cache.admins_startup_load()

    assert "admins" not in mock_app.extensions
    cache_path = state_dir / "cache" / "admins.json"
    assert not cache_path.exists()


@pytest.mark.asyncio
async def test_admins_startup_load_uses_cache_when_present(
    state_dir: pathlib.Path, mock_app: MockApp, monkeypatch: "MonkeyPatch"
):
    ldap_called = False

    async def mock_fetch_admin_users() -> frozenset[str]:
        nonlocal ldap_called
        ldap_called = True
        return frozenset({"from_ldap"})

    monkeypatch.setattr("atr.ldap.fetch_admin_users", mock_fetch_admin_users)

    await cache.admins_save_to_file(frozenset({"cached_alice", "cached_bob"}))
    await cache.admins_startup_load()

    assert ldap_called is False
    assert mock_app.extensions["admins"] == frozenset({"cached_alice", "cached_bob"})


@pytest.mark.asyncio
async def test_email_uid_read_ignores_empty_disk_cache(state_dir: pathlib.Path):
    cache_path = state_dir / "secrets" / "cached" / "email_uid.json"
    cache_path.parent.mkdir(parents=True)
    cache_data = cache.EmailUidCache(
        refreshed=datetime.datetime.now(datetime.UTC),
        hashes={},
        reverse={},
    )
    cache_path.write_text(cache_data.model_dump_json())

    assert cache._email_uid_read_from_file() is None
    assert not cache_path.exists()
    assert await cache._email_uid_read_from_file_async() is None


@pytest.mark.asyncio
async def test_email_uid_refresh_refuses_empty_ldap_mapping(
    state_dir: pathlib.Path, mock_app: MockApp, monkeypatch: "MonkeyPatch"
):
    async def email_to_uid_map() -> dict[str, str]:
        return {}

    monkeypatch.setattr("atr.util.email_to_uid_map", email_to_uid_map)

    with pytest.raises(RuntimeError, match="no usable LDAP email mappings"):
        await cache.email_uid_refresh()

    cache_path = state_dir / "secrets" / "cached" / "email_uid.json"
    assert not cache_path.exists()
    assert "email_uid_hashes" not in mock_app.extensions
    assert "email_uid_reverse" not in mock_app.extensions


@pytest.mark.asyncio
async def test_email_uid_startup_load_refreshes_ldap_instead_of_reading_disk(
    state_dir: pathlib.Path, mock_app: MockApp, monkeypatch: "MonkeyPatch"
):
    old_hash = cache._email_uid_hash("old@example.org")
    new_hash = cache._email_uid_hash("new@example.org")

    async def email_to_uid_map() -> dict[str, str]:
        return {"new@example.org": "new"}

    monkeypatch.setattr("atr.util.email_to_uid_map", email_to_uid_map)

    await cache.email_uid_save_to_file({old_hash: "old"}, {"old": [old_hash]})
    await cache.email_uid_startup_load()

    assert mock_app.extensions["email_uid_hashes"] == {new_hash: "new"}
    assert mock_app.extensions["email_uid_reverse"] == {"new": [new_hash]}


@pytest.mark.asyncio
async def test_email_uid_view_reloads_changed_disk_cache(state_dir: pathlib.Path, mock_app: MockApp):
    old_hash = cache._email_uid_hash("old@example.org")
    new_hash = cache._email_uid_hash("new@example.org")
    mock_app.extensions["email_uid_hashes"] = {old_hash: "old"}
    mock_app.extensions["email_uid_reverse"] = {"old": [old_hash]}
    mock_app.extensions["email_uid_file_mtime_ns"] = 0

    await cache.email_uid_save_to_file({new_hash: "new"}, {"new": [new_hash]})

    lookup = cache.email_uid_view()

    assert lookup.get("new@example.org") == "new"
    assert lookup.get("old@example.org") is None
    assert mock_app.extensions["email_uid_hashes"] == {new_hash: "new"}
