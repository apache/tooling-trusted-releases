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
import unittest.mock as mock

import aiohttp
import pytest

import atr.models.args as args
import atr.models.safe as safe
import atr.sbom.models.osv
import atr.sbom.osv as osv
import atr.tasks.sbom as tasks_sbom


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


async def test_fetch_vulnerability_details_times_out(monkeypatch):
    async def slow_request(*_args, **_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(osv, "_VULNERABILITY_DETAIL_TIMEOUT", 0.01)
    monkeypatch.setattr(osv, "_request_json", slow_request)

    assert await osv._fetch_vulnerability_details(mock.Mock(), "GHSA-x") is None


async def test_osv_scan_reports_truncated_response_as_unavailable(tmp_path, monkeypatch):
    rel_path = safe.RelPath("artifact.cdx.json")
    (tmp_path / str(rel_path)).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tasks_sbom, "_release_is_embargoed", mock.AsyncMock(return_value=False))
    monkeypatch.setattr(tasks_sbom.paths, "get_unfinished_dir_for", lambda *_args, **_kwargs: safe.StatePath(tmp_path))
    monkeypatch.setattr(tasks_sbom.sbom.utilities, "path_to_bundle", lambda _path: mock.sentinel.bundle)
    monkeypatch.setattr(
        tasks_sbom.sbom.osv,
        "scan_bundle",
        mock.AsyncMock(side_effect=aiohttp.ClientPayloadError()),
    )
    task_args = args.FileArgs(
        project_key=safe.ProjectKey("project"),
        version_key=safe.VersionKey("1.0.0"),
        revision_number=safe.RevisionNumber("00001"),
        file_path=rel_path,
        asf_uid="test",
    )

    with pytest.raises(
        tasks_sbom.SBOMScanningError,
        match="The OSV service could not be reached; try the scan again later",
    ):
        await tasks_sbom.osv_scan(task_args.model_dump())


async def test_paginate_query_respects_component_cap(monkeypatch):
    page = atr.sbom.models.osv.QueryResult(vulns=[_vuln("A"), _vuln("B"), _vuln("C")], next_page_token="more")
    fetch = mock.AsyncMock(return_value=[page])
    monkeypatch.setattr(osv, "_fetch_vulnerabilities_for_batch", fetch)
    monkeypatch.setattr(osv, "_MAX_VULNERABILITIES_PER_COMPONENT", 3)

    vulns = await osv._paginate_query(mock.Mock(), {"package": {}}, "token", 1)

    assert [v.id for v in vulns] == ["A", "B"]
    assert fetch.await_count == 1


async def test_paginate_query_stops_at_page_limit(monkeypatch):
    page = atr.sbom.models.osv.QueryResult(vulns=[_vuln("A")], next_page_token="more")
    fetch = mock.AsyncMock(return_value=[page])
    monkeypatch.setattr(osv, "_fetch_vulnerabilities_for_batch", fetch)
    monkeypatch.setattr(osv, "_MAX_PAGINATION_PAGES", 2)

    vulns = await osv._paginate_query(mock.Mock(), {"package": {}}, "token", 0)

    assert len(vulns) == 2
    assert fetch.await_count == 2


async def test_populate_vulnerabilities_caps_detail_fetches(monkeypatch):
    fetch = mock.AsyncMock(side_effect=lambda _session, vuln_id: _vuln(vuln_id, summary="full"))
    monkeypatch.setattr(osv, "_fetch_vulnerability_details", fetch)
    monkeypatch.setattr(osv, "_MAX_VULNERABILITY_DETAILS", 2)
    component_vulns_map = {"ref": [_vuln("A"), _vuln("B"), _vuln("C"), _vuln("A")]}

    await osv._scan_bundle_populate_vulnerabilities(mock.Mock(), component_vulns_map)

    assert [v.summary for v in component_vulns_map["ref"]] == ["full", "full", None, "full"]
    assert fetch.await_count == 2


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


def _vuln(vuln_id: str, summary: str | None = None) -> atr.sbom.models.osv.VulnerabilityDetails:
    return atr.sbom.models.osv.VulnerabilityDetails(id=vuln_id, modified="2026-01-01T00:00:00Z", summary=summary)
