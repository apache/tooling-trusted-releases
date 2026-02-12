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

"""Tests for periodic PAT cleanup of banned/deleted accounts."""

from typing import TYPE_CHECKING

import pytest

import atr.token_cleanup as token_cleanup

if TYPE_CHECKING:
    from pytest import MonkeyPatch


class MockDBSession:
    """Minimal mock for db.Session used in PAT cleanup tests."""

    def __init__(self, pat_uids: list[str] | None = None, delete_rowcount: int = 0):
        self._pat_uids = pat_uids or []
        self._delete_rowcount = delete_rowcount
        self._committed = False
        self._executed_stmts: list[object] = []

    async def execute_query(self, stmt: object) -> "MockResult":
        self._executed_stmts.append(stmt)
        # SELECT returns uid rows, DELETE returns rowcount
        stmt_type = str(type(stmt)).lower()
        if "select" in stmt_type:
            return MockResult(rows=[(uid,) for uid in self._pat_uids])
        return MockResult(rowcount=self._delete_rowcount)

    async def commit(self) -> None:
        self._committed = True

    async def __aenter__(self) -> "MockDBSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class MockResult:
    def __init__(self, rows: list[tuple[str]] | None = None, rowcount: int = 0):
        self._rows = rows or []
        self.rowcount = rowcount

    def __iter__(self):
        return iter(self._rows)


@pytest.mark.asyncio
async def test_cleanup_does_nothing_when_no_pats_exist(monkeypatch: "MonkeyPatch") -> None:
    """When no users have PATs, the cleanup should not call LDAP at all."""
    lookup_called = False

    async def mock_account_lookup(uid: str) -> dict[str, str]:
        nonlocal lookup_called
        lookup_called = True
        return {"uid": uid}

    monkeypatch.setattr("atr.token_cleanup.ldap.account_lookup", mock_account_lookup)

    mock_session = MockDBSession(pat_uids=[])
    monkeypatch.setattr("atr.token_cleanup.db.session", lambda: mock_session)

    revoked = await token_cleanup.revoke_pats_for_banned_users()

    assert revoked == 0
    assert not lookup_called


@pytest.mark.asyncio
async def test_cleanup_skips_active_accounts(monkeypatch: "MonkeyPatch") -> None:
    """Active LDAP accounts should not have their PATs revoked."""
    lookup_calls: list[str] = []

    async def mock_account_lookup(uid: str) -> dict[str, str]:
        lookup_calls.append(uid)
        return {"uid": uid, "asf-banned": "no"}

    monkeypatch.setattr("atr.token_cleanup.ldap.account_lookup", mock_account_lookup)
    monkeypatch.setattr("atr.token_cleanup.ldap.is_banned", lambda account: False)
    monkeypatch.setattr("atr.token_cleanup.storage.audit", lambda **kwargs: None)

    mock_session = MockDBSession(pat_uids=["active_user"])
    monkeypatch.setattr("atr.token_cleanup.db.session", lambda: mock_session)

    revoked = await token_cleanup.revoke_pats_for_banned_users()

    assert revoked == 0
    assert "active_user" in lookup_calls
    # Only the SELECT should have been executed, no DELETE
    assert len(mock_session._executed_stmts) == 1
    assert not mock_session._committed


@pytest.mark.asyncio
async def test_cleanup_revokes_for_banned_account(monkeypatch: "MonkeyPatch") -> None:
    """Banned LDAP accounts should have all PATs deleted."""
    audit_calls: list[dict] = []

    async def mock_account_lookup(uid: str) -> dict[str, str | list[str]]:
        return {"uid": [uid], "asf-banned": "yes"}

    monkeypatch.setattr("atr.token_cleanup.ldap.account_lookup", mock_account_lookup)
    monkeypatch.setattr("atr.token_cleanup.ldap.is_banned", lambda account: True)
    monkeypatch.setattr("atr.token_cleanup.storage.audit", lambda **kwargs: audit_calls.append(kwargs))

    # Two sessions: first returns UIDs, second handles the DELETE
    call_count = 0
    select_session = MockDBSession(pat_uids=["banned_user"])
    delete_session = MockDBSession(delete_rowcount=3)

    def mock_db_session():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return select_session
        return delete_session

    monkeypatch.setattr("atr.token_cleanup.db.session", mock_db_session)

    revoked = await token_cleanup.revoke_pats_for_banned_users()

    assert revoked == 3
    assert delete_session._committed
    assert len(audit_calls) == 1
    assert audit_calls[0]["target_asf_uid"] == "banned_user"
    assert audit_calls[0]["tokens_revoked"] == 3
    assert audit_calls[0]["reason"] == "account_banned_or_deleted"


@pytest.mark.asyncio
async def test_cleanup_revokes_for_deleted_account(monkeypatch: "MonkeyPatch") -> None:
    """Accounts not found in LDAP (returns None) should have PATs deleted."""
    audit_calls: list[dict] = []

    async def mock_account_lookup(uid: str) -> None:
        return None

    monkeypatch.setattr("atr.token_cleanup.ldap.account_lookup", mock_account_lookup)
    monkeypatch.setattr("atr.token_cleanup.storage.audit", lambda **kwargs: audit_calls.append(kwargs))

    call_count = 0
    select_session = MockDBSession(pat_uids=["deleted_user"])
    delete_session = MockDBSession(delete_rowcount=2)

    def mock_db_session():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return select_session
        return delete_session

    monkeypatch.setattr("atr.token_cleanup.db.session", mock_db_session)

    revoked = await token_cleanup.revoke_pats_for_banned_users()

    assert revoked == 2
    assert delete_session._committed
    assert len(audit_calls) == 1
    assert audit_calls[0]["target_asf_uid"] == "deleted_user"
    assert audit_calls[0]["tokens_revoked"] == 2


@pytest.mark.asyncio
async def test_cleanup_continues_after_single_ldap_failure(monkeypatch: "MonkeyPatch") -> None:
    """One LDAP failure should not prevent cleanup of other users."""
    audit_calls: list[dict] = []
    lookup_calls: list[str] = []

    async def mock_account_lookup(uid: str) -> dict[str, str | list[str]] | None:
        lookup_calls.append(uid)
        if uid == "failing_user":
            raise ConnectionError("LDAP timeout")
        return None

    monkeypatch.setattr("atr.token_cleanup.ldap.account_lookup", mock_account_lookup)
    monkeypatch.setattr("atr.token_cleanup.storage.audit", lambda **kwargs: audit_calls.append(kwargs))

    call_count = 0
    select_session = MockDBSession(pat_uids=["failing_user", "deleted_user"])
    delete_session = MockDBSession(delete_rowcount=1)

    def mock_db_session():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return select_session
        return delete_session

    monkeypatch.setattr("atr.token_cleanup.db.session", mock_db_session)

    revoked = await token_cleanup.revoke_pats_for_banned_users()

    assert revoked == 1
    # Both users should have been checked
    assert "failing_user" in lookup_calls
    assert "deleted_user" in lookup_calls
    # Only the non-failing user should have been cleaned up
    assert len(audit_calls) == 1
    assert audit_calls[0]["target_asf_uid"] == "deleted_user"


@pytest.mark.asyncio
async def test_cleanup_does_not_audit_when_zero_tokens_deleted(monkeypatch: "MonkeyPatch") -> None:
    """If a banned user has 0 tokens (race condition), no audit entry should be written."""
    audit_calls: list[dict] = []

    async def mock_account_lookup(uid: str) -> None:
        return None

    monkeypatch.setattr("atr.token_cleanup.ldap.account_lookup", mock_account_lookup)
    monkeypatch.setattr("atr.token_cleanup.storage.audit", lambda **kwargs: audit_calls.append(kwargs))

    call_count = 0
    select_session = MockDBSession(pat_uids=["ghost_user"])
    delete_session = MockDBSession(delete_rowcount=0)

    def mock_db_session():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return select_session
        return delete_session

    monkeypatch.setattr("atr.token_cleanup.db.session", mock_db_session)

    revoked = await token_cleanup.revoke_pats_for_banned_users()

    assert revoked == 0
    assert len(audit_calls) == 0


@pytest.mark.asyncio
async def test_cleanup_handles_multiple_banned_users(monkeypatch: "MonkeyPatch") -> None:
    """Multiple banned users should each have their tokens revoked independently."""
    audit_calls: list[dict] = []

    async def mock_account_lookup(uid: str) -> None:
        return None

    monkeypatch.setattr("atr.token_cleanup.ldap.account_lookup", mock_account_lookup)
    monkeypatch.setattr("atr.token_cleanup.storage.audit", lambda **kwargs: audit_calls.append(kwargs))

    call_count = 0
    select_session = MockDBSession(pat_uids=["user_a", "user_b", "user_c"])
    delete_sessions = [
        MockDBSession(delete_rowcount=2),
        MockDBSession(delete_rowcount=1),
        MockDBSession(delete_rowcount=4),
    ]

    def mock_db_session():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return select_session
        return delete_sessions[call_count - 2]

    monkeypatch.setattr("atr.token_cleanup.db.session", mock_db_session)

    revoked = await token_cleanup.revoke_pats_for_banned_users()

    assert revoked == 7
    assert len(audit_calls) == 3
    revoked_uids = {call["target_asf_uid"] for call in audit_calls}
    assert revoked_uids == {"user_a", "user_b", "user_c"}
