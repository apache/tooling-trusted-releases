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

import asyncio
import json
import pathlib
from typing import Any

import aiohttp.web as web
import pytest

import atr.pubsub as pubsub

_EVENT = {"pubsub_topics": ["commit", "svn"], "pubsub_path": "/commit/svn", "commit": {"id": 1}}
_STILLALIVE = {"stillalive": 1234.5}


class Server:
    def __init__(self) -> None:
        self.connections = 0
        self.cursors: list[str | None] = []
        self.reject = 0
        self.scripts: list[list[bytes]] = []
        self.url = ""

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.connections += 1
        self.cursors.append(request.headers.get("X-Fetch-Since-Cursor"))
        if self.reject > 0:
            self.reject -= 1
            return web.Response(status=503)
        script = self.scripts.pop(0) if self.scripts else []
        resp = web.StreamResponse()
        resp.content_type = "application/vnd.pypubsub-stream"
        await resp.prepare(request)
        for line in script:
            await resp.write(line)
        return resp


@pytest.fixture
async def server():
    s = Server()
    app = web.Application()
    app.router.add_get("/{topics:.*}", s.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    s.url = f"http://127.0.0.1:{port}/commit/svn,ldap"
    yield s
    await runner.cleanup()


async def test_cursor_file_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(pubsub, "_cursor_path", lambda: tmp_path / "pubsub-cursor.json")
    assert await pubsub._cursor_load("stream") is None
    await pubsub._cursor_save("stream", "a" * 36)
    assert await pubsub._cursor_load("stream") == "a" * 36
    assert await pubsub._cursor_load("other") is None
    await pubsub._cursor_save("stream", "bad")
    with pytest.raises(ValueError):
        await pubsub._cursor_load("stream")
    (tmp_path / "pubsub-cursor.json").write_text("garbage")
    with pytest.raises(json.JSONDecodeError):
        await pubsub._cursor_load("stream")


async def test_listen_reconnects_after_eof(server: Server) -> None:
    server.scripts = [[_line(_EVENT)], [_line(_EVENT)]]
    payloads = await _collect(server.url, 2)
    assert [p.get("commit") for p in payloads] == [{"id": 1}, {"id": 1}]
    assert server.connections == 2


async def test_listen_requests_replay_from_cursor(server: Server) -> None:
    server.scripts = [[_line(_EVENT)]]
    payloads = await _collect(server.url, 1, cursor="c" * 36)
    assert payloads[0]["commit"] == {"id": 1}
    assert server.cursors == ["c" * 36]


async def test_listen_survives_bad_status(server: Server) -> None:
    server.reject = 1
    server.scripts = [[_line(_EVENT)]]
    payloads = await _collect(server.url, 1)
    assert payloads[0]["commit"] == {"id": 1}
    assert server.connections == 2


async def test_listen_survives_non_object_line(server: Server) -> None:
    server.scripts = [[b'"not an object"\n'], [_line(_EVENT)]]
    payloads = await _collect(server.url, 1)
    assert payloads[0]["commit"] == {"id": 1}
    assert server.connections == 2


async def test_listen_survives_truncated_line(server: Server) -> None:
    server.scripts = [[b'{"truncated'], [_line(_EVENT)]]
    payloads = await _collect(server.url, 1)
    assert payloads[0]["commit"] == {"id": 1}
    assert server.connections == 2


async def test_listen_yields_events_and_keepalives(server: Server) -> None:
    server.scripts = [[_line(_STILLALIVE), _line(_EVENT)]]
    payloads = await _collect(server.url, 2)
    assert payloads[0] == _STILLALIVE
    assert payloads[1]["pubsub_topics"] == ["commit", "svn"]
    assert server.connections == 1


async def test_start_checkpoints_at_keepalives(monkeypatch: pytest.MonkeyPatch) -> None:
    saves: list[tuple[str, str]] = []

    async def fake_listen(url: str, username: str | None = None, password: str | None = None, cursor: Any = None):
        assert cursor() == "resume-cursor"
        yield {"pubsub_topics": ["commit", "svn"], "pubsub_cursor": "a" * 36}
        assert cursor() == "resume-cursor"
        yield {"stillalive": 1.0}
        assert cursor() == "a" * 36
        yield {"pubsub_topics": ["commit", "svn"], "pubsub_cursor": "b" * 36}

    async def fake_handle(payload: dict[str, Any]) -> None:
        return None

    async def fake_load(stream: str) -> str | None:
        return "resume-cursor"

    async def fake_save(stream: str, cursor: str) -> None:
        saves.append((stream, cursor))

    monkeypatch.setattr(pubsub, "listen", fake_listen)
    monkeypatch.setattr(pubsub, "_cursor_load", fake_load)
    monkeypatch.setattr(pubsub, "_cursor_save", fake_save)
    monkeypatch.setattr(pubsub.commits, "handle", fake_handle)
    listener = pubsub.PubSubListener("https://pubsub.invalid/", "user", "password")
    await listener.start()
    assert saves == [("https://pubsub.invalid/commit/svn,ldap", "a" * 36)]
    assert listener.cursor == "a" * 36
    assert listener.staged == "b" * 36


async def test_start_does_not_checkpoint_failed_events(monkeypatch: pytest.MonkeyPatch) -> None:
    # Failed events are logged in full detail
    # They must be handled by admins manually
    saves: list[tuple[str, str]] = []

    async def fake_listen(url: str, username: str | None = None, password: str | None = None, cursor: Any = None):
        yield {"pubsub_topics": ["commit", "svn"], "pubsub_cursor": "a" * 36}
        yield {"stillalive": 1.0}
        yield {"pubsub_topics": ["commit", "svn"], "pubsub_cursor": "b" * 36}
        yield {"stillalive": 2.0}

    async def fake_handle(payload: dict[str, Any]) -> None:
        if payload["pubsub_cursor"] == "a" * 36:
            raise RuntimeError("boom")

    async def fake_load(stream: str) -> str | None:
        return None

    async def fake_record(payload: dict[str, Any], detail: str) -> None:
        return None

    async def fake_save(stream: str, cursor: str) -> None:
        saves.append((stream, cursor))

    monkeypatch.setattr(pubsub, "listen", fake_listen)
    monkeypatch.setattr(pubsub, "_cursor_load", fake_load)
    monkeypatch.setattr(pubsub, "_cursor_save", fake_save)
    monkeypatch.setattr(pubsub, "_failure_record", fake_record)
    monkeypatch.setattr(pubsub.commits, "handle", fake_handle)
    listener = pubsub.PubSubListener("https://pubsub.invalid/", "user", "password")
    await listener.start()
    assert [cursor for _, cursor in saves] == ["b" * 36]
    assert listener.cursor == "b" * 36


async def test_start_does_not_checkpoint_on_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    saves: list[tuple[str, str]] = []

    async def fake_listen(url: str, username: str | None = None, password: str | None = None, cursor: Any = None):
        yield {"pubsub_topics": ["commit", "svn"], "pubsub_cursor": "a" * 36}
        raise asyncio.CancelledError

    async def fake_handle(payload: dict[str, Any]) -> None:
        return None

    async def fake_load(stream: str) -> str | None:
        return None

    async def fake_save(stream: str, cursor: str) -> None:
        saves.append((stream, cursor))

    monkeypatch.setattr(pubsub, "listen", fake_listen)
    monkeypatch.setattr(pubsub, "_cursor_load", fake_load)
    monkeypatch.setattr(pubsub, "_cursor_save", fake_save)
    monkeypatch.setattr(pubsub.commits, "handle", fake_handle)
    listener = pubsub.PubSubListener("https://pubsub.invalid/", "user", "password")
    with pytest.raises(asyncio.CancelledError):
        await listener.start()
    assert saves == []
    assert listener.staged == "a" * 36
    assert listener.cursor is None


async def test_start_halts_when_cursor_load_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    halts: list[str] = []
    handled: list[int] = []

    async def fake_halt(detail: str) -> None:
        halts.append(detail)

    async def fake_handle(payload: dict[str, Any]) -> None:
        handled.append(payload["n"])

    async def fake_listen(url: str, username: str | None = None, password: str | None = None, cursor: Any = None):
        yield {"pubsub_topics": ["commit", "svn"], "n": 1}

    async def fake_load(stream: str) -> str | None:
        raise ValueError("damaged")

    monkeypatch.setattr(pubsub, "listen", fake_listen)
    monkeypatch.setattr(pubsub, "_cursor_load", fake_load)
    monkeypatch.setattr(pubsub, "_halt_notify", fake_halt)
    monkeypatch.setattr(pubsub.commits, "handle", fake_handle)
    await pubsub.PubSubListener("https://pubsub.invalid/", "user", "password").start()
    assert halts == ["ValueError: damaged"]
    assert handled == []


async def test_start_ignores_invalid_cursors(monkeypatch: pytest.MonkeyPatch) -> None:
    saves: list[tuple[str, str]] = []

    async def fake_listen(url: str, username: str | None = None, password: str | None = None, cursor: Any = None):
        yield {"pubsub_topics": ["commit", "svn"], "pubsub_cursor": "bad\ncursor"}
        yield {"stillalive": 1.0}
        yield {"pubsub_topics": ["commit", "svn"], "pubsub_cursor": "a" * 36}
        yield {"stillalive": 2.0}

    async def fake_handle(payload: dict[str, Any]) -> None:
        return None

    async def fake_load(stream: str) -> str | None:
        return None

    async def fake_save(stream: str, cursor: str) -> None:
        saves.append((stream, cursor))

    monkeypatch.setattr(pubsub, "listen", fake_listen)
    monkeypatch.setattr(pubsub, "_cursor_load", fake_load)
    monkeypatch.setattr(pubsub, "_cursor_save", fake_save)
    monkeypatch.setattr(pubsub.commits, "handle", fake_handle)
    listener = pubsub.PubSubListener("https://pubsub.invalid/", "user", "password")
    await listener.start()
    assert [cursor for _, cursor in saves] == ["a" * 36]
    assert listener.cursor == "a" * 36


async def test_start_isolates_handler_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    handled = []
    records: list[tuple[dict[str, Any], str]] = []

    async def fake_listen(url: str, username: str | None = None, password: str | None = None, cursor: Any = None):
        yield {"pubsub_topics": ["commit", "svn"], "n": 1}
        yield {"stillalive": 1.0}
        yield {"pubsub_topics": ["commit", "svn"], "n": 2}

    async def fake_handle(payload: dict[str, Any]) -> None:
        if payload["n"] == 1:
            raise RuntimeError("boom")
        handled.append(payload["n"])

    async def fake_load(stream: str) -> str | None:
        return None

    async def fake_record(payload: dict[str, Any], detail: str) -> None:
        records.append((payload, detail))

    monkeypatch.setattr(pubsub, "listen", fake_listen)
    monkeypatch.setattr(pubsub, "_cursor_load", fake_load)
    monkeypatch.setattr(pubsub, "_failure_record", fake_record)
    monkeypatch.setattr(pubsub.commits, "handle", fake_handle)
    await pubsub.PubSubListener("https://pubsub.invalid/", "user", "password").start()
    assert handled == [2]
    assert len(records) == 1
    assert records[0][0]["n"] == 1
    assert "boom" in records[0][1]


async def test_start_logs_handler_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    records: list[tuple[dict[str, Any], str]] = []
    saves: list[tuple[str, str]] = []
    warnings: list[tuple[str, dict[str, Any]]] = []

    async def fake_listen(url: str, username: str | None = None, password: str | None = None, cursor: Any = None):
        yield {"pubsub_topics": ["commit", "svn"], "pubsub_cursor": "a" * 36}
        yield {"stillalive": 1.0}

    async def fake_handle(payload: dict[str, Any]) -> str | None:
        return "problem detail"

    async def fake_load(stream: str) -> str | None:
        return None

    async def fake_record(payload: dict[str, Any], detail: str) -> None:
        records.append((payload, detail))

    async def fake_save(stream: str, cursor: str) -> None:
        saves.append((stream, cursor))

    monkeypatch.setattr(pubsub, "listen", fake_listen)
    monkeypatch.setattr(pubsub, "_cursor_load", fake_load)
    monkeypatch.setattr(pubsub, "_cursor_save", fake_save)
    monkeypatch.setattr(pubsub, "_failure_record", fake_record)
    monkeypatch.setattr(pubsub.commits, "handle", fake_handle)
    monkeypatch.setattr(pubsub.log, "warning", lambda msg, **kwargs: warnings.append((msg, kwargs)))
    await pubsub.PubSubListener("https://pubsub.invalid/", "user", "password").start()
    assert any("problem detail" in msg for msg, _ in warnings)
    assert all("payload" not in kwargs for _, kwargs in warnings)
    assert records == [({"pubsub_topics": ["commit", "svn"], "pubsub_cursor": "a" * 36}, "problem detail")]
    assert [cursor for _, cursor in saves] == ["a" * 36]


async def test_start_retries_cursor_save_without_stopping(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[tuple[str, str]] = []
    handled: list[int] = []

    async def fake_listen(url: str, username: str | None = None, password: str | None = None, cursor: Any = None):
        yield {
            "pubsub_topics": ["commit", "svn"],
            "pubsub_cursor": "a" * 36,
            "n": 1,
        }
        yield {"stillalive": 1.0}
        yield {"stillalive": 2.0}
        yield {
            "pubsub_topics": ["commit", "svn"],
            "pubsub_cursor": "b" * 36,
            "n": 2,
        }

    async def fake_handle(payload: dict[str, Any]) -> None:
        handled.append(payload["n"])

    async def fake_load(stream: str) -> str | None:
        return None

    async def fake_save(stream: str, cursor: str) -> None:
        attempts.append((stream, cursor))
        if len(attempts) == 1:
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(pubsub, "listen", fake_listen)
    monkeypatch.setattr(pubsub, "_cursor_load", fake_load)
    monkeypatch.setattr(pubsub, "_cursor_save", fake_save)
    monkeypatch.setattr(pubsub.commits, "handle", fake_handle)
    listener = pubsub.PubSubListener("https://pubsub.invalid/", "user", "password")
    await listener.start()
    assert attempts == [
        ("https://pubsub.invalid/commit/svn,ldap", "a" * 36),
        ("https://pubsub.invalid/commit/svn,ldap", "a" * 36),
    ]
    assert handled == [1, 2]
    assert listener.cursor == "a" * 36
    assert listener.staged == "b" * 36


async def _collect(url: str, count: int, cursor: str | None = None) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    gen = pubsub.listen(url, cursor=(lambda: cursor) if cursor else None)
    async with asyncio.timeout(5):
        async for payload in gen:
            payloads.append(payload)
            if len(payloads) == count:
                break
        await gen.aclose()
    return payloads


def _line(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode() + b"\n"
