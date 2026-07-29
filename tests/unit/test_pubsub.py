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
from typing import Any

import aiohttp.web as web
import pytest

import atr.pubsub as pubsub

_EVENT = {"pubsub_topics": ["commit", "svn"], "pubsub_path": "/commit/svn", "commit": {"id": 1}}
_STILLALIVE = {"stillalive": 1234.5}


class Server:
    def __init__(self) -> None:
        self.connections = 0
        self.scripts: list[list[bytes]] = []
        self.url = ""

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.connections += 1
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


async def test_listen_reconnects_after_eof(server: Server) -> None:
    server.scripts = [[_line(_EVENT)], [_line(_EVENT)]]
    payloads = await _collect(server.url, 2)
    assert [p.get("commit") for p in payloads] == [{"id": 1}, {"id": 1}]
    assert server.connections == 2


async def test_listen_yields_events_and_keepalives(server: Server) -> None:
    server.scripts = [[_line(_STILLALIVE), _line(_EVENT)]]
    payloads = await _collect(server.url, 2)
    assert payloads[0] == _STILLALIVE
    assert payloads[1]["pubsub_topics"] == ["commit", "svn"]
    assert server.connections == 1


async def _collect(url: str, count: int) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    gen = pubsub.listen(url)
    async with asyncio.timeout(5):
        async for payload in gen:
            if payload is None:
                continue
            payloads.append(payload)
            if len(payloads) == count:
                break
        await gen.aclose()
    return payloads


def _line(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode() + b"\n"
