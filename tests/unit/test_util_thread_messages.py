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

import json

import pytest

import atr.util as util


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self._payload

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, _url: str):
        return FakeResponse(self._payload)


@pytest.mark.asyncio
async def test_thread_messages_raises_on_partial_email_fetch_when_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_get_urls_as_completed(_urls):
        yield "https://lists.apache.org/api/email.json?id=missing", 500, b""

    monkeypatch.setattr(
        util,
        "create_secure_session",
        lambda timeout=None, ipv4_only=False: FakeSession({"emails": [{"id": "missing"}]}),
    )
    monkeypatch.setattr(util, "get_urls_as_completed", _fake_get_urls_as_completed)

    with pytest.raises(util.FetchError, match="Failed to fetch email data"):
        _messages = [message async for message in util.thread_messages("threadid", strict=True)]


@pytest.mark.asyncio
async def test_thread_messages_raises_on_partial_email_parse_when_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_get_urls_as_completed(_urls):
        yield "https://lists.apache.org/api/email.json?id=broken", 200, b"{not json"

    monkeypatch.setattr(
        util,
        "create_secure_session",
        lambda timeout=None, ipv4_only=False: FakeSession({"emails": [{"id": "broken"}]}),
    )
    monkeypatch.setattr(util, "get_urls_as_completed", _fake_get_urls_as_completed)

    with pytest.raises(util.FetchError, match="Failed to parse email JSON"):
        _messages = [message async for message in util.thread_messages("threadid", strict=True)]


@pytest.mark.asyncio
async def test_thread_messages_skips_partial_email_fetches_when_not_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_get_urls_as_completed(_urls):
        yield "https://lists.apache.org/api/email.json?id=missing", 500, b""
        yield "https://lists.apache.org/api/email.json?id=ok", 200, json.dumps({"id": "ok", "epoch": 1}).encode()

    monkeypatch.setattr(
        util,
        "create_secure_session",
        lambda timeout=None, ipv4_only=False: FakeSession({"emails": [{"id": "missing"}, {"id": "ok"}]}),
    )
    monkeypatch.setattr(util, "get_urls_as_completed", _fake_get_urls_as_completed)

    messages = [message async for message in util.thread_messages("threadid", strict=False)]

    assert messages == [("ok", {"id": "ok", "epoch": 1})]
