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

"""Tests for SSH rate limiting."""

import unittest.mock as mock
from typing import TYPE_CHECKING

import asyncssh
import pytest

import atr.ssh as ssh

if TYPE_CHECKING:
    import collections

    from pytest import MonkeyPatch


def test_auth_completed_allows_within_user_limit():
    server = _make_server()
    server._conn = _make_conn_with_username("alice")
    server.auth_completed()
    server._conn.disconnect.assert_not_called()


def test_auth_completed_blocks_when_user_limit_exceeded():
    for _ in range(ssh._RATE_LIMIT_USER):
        server = _make_server()
        server._conn = _make_conn_with_username("alice")
        server.auth_completed()
    server = _make_server()
    server._conn = _make_conn_with_username("alice")
    server.auth_completed()
    server._conn.disconnect.assert_called_once_with(asyncssh.DISC_TOO_MANY_CONNECTIONS, "Rate limit exceeded")


def test_auth_completed_different_users_are_independent():
    for _ in range(ssh._RATE_LIMIT_USER):
        server = _make_server()
        server._conn = _make_conn_with_username("alice")
        server.auth_completed()
    server = _make_server()
    server._conn = _make_conn_with_username("bob")
    server.auth_completed()
    server._conn.disconnect.assert_not_called()


def test_auth_completed_github_uses_asf_uid_for_rate_limit():
    for _ in range(ssh._RATE_LIMIT_USER):
        server = _make_server()
        server._conn = _make_conn_with_username("github")
        server._github_asf_uid = "alice"
        server.auth_completed()
    server = _make_server()
    server._conn = _make_conn_with_username("github")
    server._github_asf_uid = "alice"
    server.auth_completed()
    server._conn.disconnect.assert_called_once_with(asyncssh.DISC_TOO_MANY_CONNECTIONS, "Rate limit exceeded")


def test_connection_made_allows_within_ip_limit():
    server = _make_server()
    conn = _make_conn_with_ip("1.2.3.4")
    server.connection_made(conn)
    conn.disconnect.assert_not_called()


def test_connection_made_blocks_when_ip_limit_exceeded():
    for _ in range(ssh._RATE_LIMIT_IP):
        server = _make_server()
        server.connection_made(_make_conn_with_ip("1.2.3.4"))
    server = _make_server()
    conn = _make_conn_with_ip("1.2.3.4")
    server.connection_made(conn)
    conn.disconnect.assert_called_once_with(asyncssh.DISC_TOO_MANY_CONNECTIONS, "Rate limit exceeded")


def test_connection_made_different_ips_are_independent():
    for _ in range(ssh._RATE_LIMIT_IP):
        server = _make_server()
        server.connection_made(_make_conn_with_ip("1.2.3.4"))
    server = _make_server()
    conn = _make_conn_with_ip("5.6.7.8")
    server.connection_made(conn)
    conn.disconnect.assert_not_called()


def test_rate_limit_check_allows_after_window_expires(monkeypatch: "MonkeyPatch"):
    current_time = [1000.0]
    monkeypatch.setattr("atr.ssh.time.monotonic", lambda: current_time[0])
    bucket: dict[str, collections.deque[float]] = {}
    for _ in range(ssh._RATE_LIMIT_USER):
        ssh._rate_limit_check(bucket, "alice", ssh._RATE_LIMIT_USER)
    assert ssh._rate_limit_check(bucket, "alice", ssh._RATE_LIMIT_USER) is False

    current_time[0] = 1000.0 + ssh._RATE_WINDOW + 1.0
    assert ssh._rate_limit_check(bucket, "alice", ssh._RATE_LIMIT_USER) is True


def test_rate_limit_check_allows_within_limit():
    bucket: dict[str, collections.deque[float]] = {}
    for _ in range(ssh._RATE_LIMIT_USER):
        assert ssh._rate_limit_check(bucket, "alice", ssh._RATE_LIMIT_USER) is True


def test_rate_limit_check_blocks_when_limit_reached():
    bucket: dict[str, collections.deque[float]] = {}
    for _ in range(ssh._RATE_LIMIT_USER):
        ssh._rate_limit_check(bucket, "alice", ssh._RATE_LIMIT_USER)
    assert ssh._rate_limit_check(bucket, "alice", ssh._RATE_LIMIT_USER) is False


def test_rate_limit_check_keys_are_independent():
    bucket: dict[str, collections.deque[float]] = {}
    for _ in range(ssh._RATE_LIMIT_USER):
        ssh._rate_limit_check(bucket, "alice", ssh._RATE_LIMIT_USER)
    assert ssh._rate_limit_check(bucket, "alice", ssh._RATE_LIMIT_USER) is False
    assert ssh._rate_limit_check(bucket, "bob", ssh._RATE_LIMIT_USER) is True


@pytest.fixture(autouse=True)
def _clear_rate_buckets():
    """Clear global rate limit buckets before each test to prevent state leakage."""
    ssh.global_ip_rate_buckets.clear()
    ssh.global_user_rate_buckets.clear()
    yield
    ssh.global_ip_rate_buckets.clear()
    ssh.global_user_rate_buckets.clear()


def _make_conn_with_ip(ip: str) -> mock.MagicMock:
    conn = mock.MagicMock(spec=asyncssh.SSHServerConnection)
    conn.get_extra_info.return_value = (ip, 22)
    return conn


def _make_conn_with_username(username: str) -> mock.MagicMock:
    conn = mock.MagicMock(spec=asyncssh.SSHServerConnection)
    conn.get_extra_info.return_value = username
    return conn


def _make_server() -> ssh.SSHServer:
    server = ssh.SSHServer.__new__(ssh.SSHServer)
    server._github_asf_uid = None
    server._github_payload = None
    return server
