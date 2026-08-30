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
import io
import tempfile
import types

import pytest
import quart
import quart.datastructures as datastructures
import werkzeug.exceptions as exceptions

import atr.body as body


@pytest.fixture
def state_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(body, "spool_dir", lambda: str(tmp_path))
    return tmp_path


async def test_body_append_after_close(state_tmp):
    b = body.Body(None, None)
    b.append(b"data")
    b.close()
    b.append(b"more")


async def test_body_await_retains(state_tmp):
    b = body.Body(None, None)
    b.append(b"alpha")
    b.set_complete()
    assert await b == b"alpha"
    assert await b == b"alpha"


async def test_body_iterate_then_await(state_tmp):
    b = body.Body(None, None)
    b.append(b"alpha")
    b.append(b"beta")
    b.set_complete()
    chunks = [chunk async for chunk in b]
    assert b"".join(chunks) == b"alphabeta"
    assert await b == b""


async def test_body_limit_known_length(state_tmp):
    b = body.Body(20, 10)
    with pytest.raises(exceptions.RequestEntityTooLarge):
        await b


async def test_body_limit_wakes_reader(state_tmp):
    b = body.Body(None, 10)
    b.append(b"x" * 6)
    assert await anext(b) == b"x" * 6
    task = asyncio.ensure_future(anext(b))
    await asyncio.sleep(0)
    b.append(b"x" * 5)
    with pytest.raises(exceptions.RequestEntityTooLarge):
        await task


async def test_body_low_disk_refused(state_tmp, monkeypatch):
    calls = []

    def fake_disk_usage(path):
        calls.append(path)
        return types.SimpleNamespace(free=body.spool_floor() + body.SPOOL_MAX_SIZE)

    monkeypatch.setattr(body.shutil, "disk_usage", fake_disk_usage)
    b = body.Body(None, None)
    b.append(b"x" * body.SPOOL_MAX_SIZE)
    assert calls == []
    b.append(b"x")
    with pytest.raises(exceptions.ServiceUnavailable) as exc_info:
        await anext(b)
    assert "disk space" in exc_info.value.description


async def test_body_spools_to_disk(state_tmp, monkeypatch):
    calls = []
    original = tempfile.TemporaryFile

    def recorder(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(body.shutil, "disk_usage", lambda path: types.SimpleNamespace(free=body.spool_floor() * 4))
    monkeypatch.setattr(tempfile, "TemporaryFile", recorder)
    b = body.Body(None, None)
    payload = b"x" * (body.SPOOL_MAX_SIZE + 1)
    b.append(payload)
    b.set_complete()
    chunks = [chunk async for chunk in b]
    assert len(calls) == 1
    assert calls[0]["dir"] == str(state_tmp)
    assert all(len(chunk) <= body.READ_CHUNK_SIZE for chunk in chunks)
    assert b"".join(chunks) == payload
    assert b._spool.seek(0, io.SEEK_END) == 0


async def test_request_multipart(state_tmp):
    app = quart.Quart(__name__)
    app.request_class = body.Request
    received = {}

    @app.post("/upload")
    async def upload():
        files = await quart.request.files
        fs = files["file"]
        received["body"] = quart.request.body
        received["content"] = fs.stream.read()
        received["stream"] = fs.stream
        return ""

    payload = b"z" * 200000
    client = app.test_client()
    response = await client.post(
        "/upload",
        files={"file": datastructures.FileStorage(io.BytesIO(payload), filename="a.bin")},
    )
    assert response.status_code == 200
    assert received["content"] == payload
    assert received["body"]._spool.closed
    assert received["stream"].closed
    assert not isinstance(received["stream"], tempfile.SpooledTemporaryFile)
