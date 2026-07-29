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

import aiohttp
import pytest

import atr.sbom.osv as osv


class FakeResponse:
    def __init__(self, status: int, payload=None, headers=None):
        self.status = status
        self._payload = payload
        self._headers = headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(None, (), status=self.status, headers=self._headers)


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = 0

    def request(self, method, url, json=None):
        self.requests += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []

    async def fake_sleep(delay):
        recorded.append(delay)

    monkeypatch.setattr(osv.asyncio, "sleep", fake_sleep)
    return recorded


async def test_request_json_does_not_retry_client_errors(sleeps):
    session = FakeSession([FakeResponse(404)])

    with pytest.raises(aiohttp.ClientResponseError):
        await osv._request_json(session, "GET", "https://osv.test/vulns/x")
    assert session.requests == 1
    assert sleeps == []


async def test_request_json_gives_up_after_exhausting_retries(sleeps):
    session = FakeSession([aiohttp.ClientConnectionError(), TimeoutError(), FakeResponse(503)])

    with pytest.raises(aiohttp.ClientResponseError):
        await osv._request_json(session, "GET", "https://osv.test/vulns/x")
    assert session.requests == 3
    assert sleeps == list(osv._RETRY_DELAY_SECONDS)


async def test_request_json_retries_server_errors(sleeps):
    session = FakeSession([FakeResponse(500), FakeResponse(429, headers={"Retry-After": "3"}), FakeResponse(200, {})])

    result = await osv._request_json(session, "POST", "https://osv.test/querybatch", {"queries": []})
    assert result == {}
    assert session.requests == 3
    assert sleeps == list(osv._RETRY_DELAY_SECONDS)


async def test_request_json_retries_truncated_responses(sleeps):
    session = FakeSession([FakeResponse(200, aiohttp.ClientPayloadError()), FakeResponse(200, {})])

    assert await osv._request_json(session, "GET", "https://osv.test/vulns/x") == {}
    assert session.requests == 2
    assert sleeps == [osv._RETRY_DELAY_SECONDS[0]]


async def test_request_json_stops_when_retry_after_exceeds_delay(sleeps):
    session = FakeSession([FakeResponse(429, headers={"Retry-After": "2"})])

    with pytest.raises(aiohttp.ClientResponseError):
        await osv._request_json(session, "GET", "https://osv.test/vulns/x")
    assert session.requests == 1
    assert sleeps == []
