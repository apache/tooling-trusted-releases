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
import datetime
import hashlib
import json
import unittest.mock as mock

import aiohttp
import pytest

import atr.models.results as results
import atr.sbom.heatmap as heatmap

_JSON_URL = "https://downloads.apache.org/example/a.cdx.json"
_XML_URL = "https://archive.apache.org/dist/example/b.cdx.xml"
_KEY = "pkg:maven/org.example/library"
_PROJECT = "github.com/apache/example"
_NOW = datetime.datetime(2026, 9, 8, tzinfo=datetime.UTC)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse(self.responses[url].pop(0))


class SlowSession(FakeSession):
    async def __aenter__(self):
        await asyncio.sleep(60)
        return self


def document(*components: dict) -> bytes:
    return json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "components": components}).encode()


def package(**changes) -> dict:
    return {
        "purl": _KEY,
        "repository_url": f"https://{_PROJECT}",
        "latest_release_published_at": "2026-08-01T00:00:00Z",
        "rankings": {"average": 0},
        "repo_metadata": repository(),
        "issue_metadata": {"active_maintainers": [{"login": "example"}]},
    } | changes


def repository(**changes) -> dict:
    return {
        "archived": False,
        "commit_stats": {"dds": 0.5},
        "metadata": {"files": {"security": "SECURITY.md", "code_of_conduct": None, "contributing": None}},
    } | changes


def row(**changes) -> results.SBOMHeatmapRow:
    return results.SBOMHeatmapRow(
        key=_KEY,
        purl=_KEY + "@1",
        source_purls=[_KEY + "@1"],
        name="library",
        ecosystem="maven",
        version="1",
        artifacts=["a.tar.gz"],
        advisory_status="not_found",
        **changes,
    )


def session(monkeypatch: pytest.MonkeyPatch, packages=None, versions=None, projects=None, **extra) -> FakeSession:
    responses = {
        f"{heatmap._ECOSYSTEMS}/packages/bulk_lookup": [packages or []],
        f"{heatmap._DEPSDEV}/purlbatch": [versions or {"responses": []}],
        f"{heatmap._DEPSDEV}/projectbatch": [projects or {"responses": []}],
    } | extra
    fake = FakeSession(responses)
    monkeypatch.setattr(heatmap.util, "create_secure_session", mock.Mock(return_value=fake))
    return fake


async def test_analyse_enforces_its_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heatmap, "_DEADLINE_SECONDS", 0.01)
    monkeypatch.setattr(heatmap.util, "create_secure_session", mock.Mock(return_value=SlowSession({})))
    with pytest.raises(TimeoutError):
        await heatmap.analyse({})


async def test_analyse_keeps_versions_membership_and_unknown_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    source = document(
        {"type": "library", "purl": _KEY + "@1?type=jar"},
        {"type": "library", "purl": _KEY + "@1"},
        {"type": "library", "purl": "pkg:pypi/Example_Package", "components": [{"purl": "pkg:npm/nested@2"}]},
        {"type": "file", "name": "README"},
        {"type": "library", "purl": "invalid"},
        {"type": "library", "purl": "pkg:generic/example@1"},
    )
    xml = (
        '<bom xmlns="http://cyclonedx.org/schema/bom/1.5" version="1"><components>'
        f'<component type="library"><name>library</name><purl>{_KEY}@2</purl></component>'
        "</components></bom>"
    ).encode()
    fetch = mock.AsyncMock(side_effect=[source, xml])
    monkeypatch.setattr(heatmap.utilities, "fetch", fetch)
    fake = session(
        monkeypatch,
        packages=[package()],
        versions={
            "responses": [
                version(_KEY + "@1", "GHSA-one"),
                version(_KEY + "@2"),
                {"request": {"purl": "pkg:npm/nested@2"}},
            ]
        },
    )

    result = await heatmap.analyse({"a.tar.gz": _JSON_URL, "b.tar.gz": _XML_URL, "c.zip": _JSON_URL})
    rows = {r.purl: r for r in result.rows}

    assert len(rows) == 5
    assert rows[_KEY + "@1"].artifacts == ["a.tar.gz", "c.zip"]
    assert rows[_KEY + "@1"].source_purls == [_KEY + "@1", _KEY + "@1?type=jar"]
    assert rows[_KEY + "@1"].advisories == ["GHSA-one"]
    assert rows[_KEY + "@2"].artifacts == ["b.tar.gz"]
    assert rows[_KEY + "@2"].advisory_status == "none"
    assert rows[_KEY + "@2"].advisories == []
    assert rows["pkg:npm/nested@2"].advisory_status == "not_found"
    assert rows["pkg:npm/nested@2"].health is None
    assert rows["pkg:pypi/example-package"].advisory_status == "version_unknown"
    assert rows["pkg:generic/example@1"].advisory_status == "unsupported"
    assert result.rows[0].advisories
    assert fetch.await_count == 2
    assert len(fake.calls) == 3
    assert result.sboms[0].components == 7
    assert result.sboms[0].files == 1
    assert result.sboms[0].packages == 4
    assert result.sboms[0].sha256 == hashlib.sha256(source).hexdigest()
    assert result.sboms[0].retrieved_at is not None
    assert results.ResultsAdapter.validate_json(result.model_dump_json()) == result


async def test_analyse_labels_gitbox_inference_and_deduplicates_repository_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = document({"purl": _KEY + "@1"}, {"purl": "pkg:maven/org.example/other@2"})
    monkeypatch.setattr(heatmap.utilities, "fetch", mock.AsyncMock(return_value=source))
    mirror_url = "https://gitbox.apache.org/repos/asf/example.git"
    packages = [
        package(repository_url=mirror_url, repo_metadata=None, issue_metadata=None),
        package(purl="pkg:maven/org.example/other", repository_url=mirror_url, repo_metadata=None, issue_metadata=None),
    ]
    fake = session(
        monkeypatch,
        packages=packages,
        versions={
            "responses": [
                {
                    "request": {"purl": _KEY + "@1"},
                    "result": {
                        "version": {
                            "relatedProjects": [{"relationType": "SOURCE_REPO", "projectKey": {"id": _PROJECT}}]
                        }
                    },
                }
            ]
        },
        projects={
            "responses": [
                {
                    "request": {"projectKey": {"id": _PROJECT}},
                    "project": {"scorecard": {"date": "2026-08-24", "checks": [{"name": "Maintained", "score": 10}]}},
                }
            ]
        },
        **{
            "https://repos.ecosyste.ms/api/v1/hosts/GitHub/repositories/apache%2Fexample": [repository()],
            "https://issues.ecosyste.ms/api/v1/hosts/GitHub/repositories/apache%2Fexample": [
                {"active_maintainers": []}
            ],
        },
    )

    result = await heatmap.analyse({"a.tar.gz": _JSON_URL})

    assert len(fake.calls) == 5
    for entry in result.rows:
        assert entry.source_repo == _PROJECT
        assert entry.repository_source == ("deps.dev" if (entry.key == _KEY) else "gitbox_mirror")
        assert entry.repository_url == mirror_url
        assert entry.active_maintainers == 0
        assert entry.health_inputs == 4
        assert entry.health is not None
        assert entry.maintained == 10
        assert entry.scorecard_date == "2026-08-24"


@pytest.mark.parametrize(
    "xml",
    [
        b"<bad",
        b'<bom xmlns="http://cyclonedx.org/schema/bom/1.5" version="1">'
        b'<externalReferences><reference type="vcs"><url>https://example.org/%</url></reference>'
        b"</externalReferences></bom>",
    ],
)
async def test_analyse_preserves_lookup_failure_and_continues_after_bad_sboms(
    monkeypatch: pytest.MonkeyPatch, xml: bytes
) -> None:
    monkeypatch.setattr(
        heatmap.utilities,
        "fetch",
        mock.AsyncMock(side_effect=[xml, b'{"spdxVersion":"SPDX-2.3"}', document({"purl": _KEY + "@1"})]),
    )
    session(monkeypatch, versions=aiohttp.ClientConnectionError("Unavailable"))

    result = await heatmap.analyse(
        {
            "a.tar.gz": _XML_URL,
            "b.tar.gz": "https://downloads.apache.org/example/unsupported.json",
            "c.tar.gz": _JSON_URL,
        }
    )

    assert all(sbom.error for sbom in result.sboms[:2])
    assert all(sbom.sha256 for sbom in result.sboms)
    assert result.rows[0].advisory_status == "failed"
    assert result.rows[0].health is None
    assert result.rows[0].artifacts == ["c.tar.gz"]


async def test_analyse_raises_when_ecosystems_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heatmap.utilities, "fetch", mock.AsyncMock(return_value=document({"purl": _KEY + "@1"})))
    session(monkeypatch, packages=aiohttp.ClientConnectionError("Unavailable"))
    with pytest.raises(ValueError, match=r"ecosyste\.ms package lookup failed"):
        await heatmap.analyse({"a.tar.gz": _JSON_URL})


async def test_analyse_raises_when_no_sbom_can_be_retrieved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heatmap.utilities, "fetch", mock.AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="No SBOM"):
        await heatmap.analyse({"a.tar.gz": _JSON_URL})


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://gitbox.apache.org/repos/asf/example.git", _PROJECT),
        ("https://gitbox.apache.org/repos/asf?p=example.git", _PROJECT),
        ("https://gitbox.apache.org/repos/asf?p=example.git&p=other.git", None),
        ("https://gitbox.apache.org/repos/asf/nested/example.git", None),
        ("https://gitbox.apache.org.example.org/repos/asf/example.git", None),
    ],
)
def test_gitbox_mapping_is_narrow(url: str, expected: str | None) -> None:
    assert heatmap._gitbox(url) == expected


def test_missing_governance_is_not_zero_and_issue_tracker_is_not_source() -> None:
    entry = row()
    data = {"relatedProjects": [{"relationType": "ISSUE_TRACKER", "projectKey": {"id": _PROJECT}}]}
    heatmap._observations(entry, {}, {entry.purl: data}, {})
    assert entry.source_repo is None
    heatmap._repository_observations(entry, {"metadata": {"files": {}}}, {})
    assert entry.governance_files is None
    heatmap._repository_observations(entry, repository(), {"active_maintainers": []})
    assert entry.governance_files == 1
    assert entry.active_maintainers == 0


@pytest.mark.parametrize(
    ("changes", "health", "inputs", "risk"),
    [
        ({}, 0.75, 4, 0.75 * (1 - 0.75)),
        ({"dds": None}, None, 3, None),
        ({"latest_release_at": "invalid"}, None, 3, None),
        ({"active_maintainers": 0, "governance_files": 0}, 0.25, 4, 0.75 * (1 - 0.25)),
        ({"archived": True, "dds": None}, 0.05, 3, 0.75 * 0.95),
        ({"rankings_average": None}, 0.75, 4, None),
    ],
)
def test_scoring_uses_observed_inputs(changes: dict, health: float | None, inputs: int, risk: float | None) -> None:
    fields = {
        "latest_release_at": _NOW.isoformat(),
        "dds": 0,
        "active_maintainers": 3,
        "governance_files": 3,
        "rankings_average": 101**0.25 - 1,
    } | changes
    entry = row(**fields)
    heatmap._score(entry, _NOW)
    assert entry.health == pytest.approx(health) if (health is not None) else entry.health is None
    assert entry.risk == pytest.approx(risk) if (risk is not None) else entry.risk is None
    assert entry.health_inputs == inputs
    heatmap._scorecard(entry, {"scorecard": {"checks": [{"name": "Maintained", "score": -1}]}})
    assert entry.maintained is None


async def test_versions_follow_batch_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = session(monkeypatch)
    fake.responses[f"{heatmap._DEPSDEV}/purlbatch"] = [
        {"responses": [version(_KEY + "@1")], "nextPageToken": "next"},
        {"responses": [version(_KEY + "@2", "GHSA-two")]},
    ]

    versions = await heatmap._versions(fake, [_KEY + "@1", _KEY + "@2"])

    assert versions[_KEY + "@2"]["advisoryKeys"] == [{"id": "GHSA-two"}]
    assert fake.calls[1][2]["json"]["pageToken"] == "next"
    assert fake.calls[0][2]["allow_redirects"] is False
    assert "mailto:dev@tooling.apache.org" in fake.calls[0][2]["headers"]["User-Agent"]


def version(purl: str, *advisories: str) -> dict:
    return {
        "request": {"purl": purl},
        "result": {"version": {"advisoryKeys": [{"id": identifier} for identifier in advisories]}},
    }
