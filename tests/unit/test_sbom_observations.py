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
import unittest.mock as mock

import aiohttp
import pytest

import atr.sbom.observations as observations
import atr.sbom.streaming as streaming


class Response:
    def __init__(self, body: object, status: int = 200):
        self.content = aiohttp.StreamReader(mock.Mock(), 2**16)
        self.content.feed_data(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.content.feed_eof()
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientError(str(self.status))


class Session:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        return self.get(url, **kwargs)


async def test_collect_batches_packages_and_preserves_missing_observations(monkeypatch) -> None:
    keys = [f"pkg:npm/p{i}" for i in range(101)]
    session = Session(
        [
            Response(
                [
                    {
                        "purl": keys[0],
                        "rankings": {"average": "0"},
                        "repo_metadata": {"archived": True},
                        "latest_release_published_at": "yesterday",
                        "repository_url": None,
                    }
                ]
            ),
            Response([]),
        ]
    )
    create = mock.Mock(return_value=session)
    monkeypatch.setattr(observations.util, "create_secure_session", create)
    result = await observations.collect(keys)
    assert set(result) == {keys[0]}
    assert result[keys[0]]["rankings_average"] == 0.0
    assert result[keys[0]]["archived"] is True
    assert result[keys[0]]["dds"] is None
    assert result[keys[0]]["latest_release_at"] == "yesterday"
    assert [len(call[1]["json"]["purls"]) for call in session.calls] == [100, 1]
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
    assert create.call_args.kwargs["public"] is True


async def test_collect_empty_never_opens_session(monkeypatch) -> None:
    create = mock.Mock()
    monkeypatch.setattr(observations.util, "create_secure_session", create)
    assert await observations.collect([]) == {}
    create.assert_not_called()


@pytest.mark.parametrize("status", [404, 429, 503])
async def test_collect_failed_bulk_is_operational_even_for_404(monkeypatch, status) -> None:
    session = Session([Response({}, status)])
    monkeypatch.setattr(observations.util, "create_secure_session", mock.Mock(return_value=session))
    with pytest.raises(aiohttp.ClientError):
        await observations.collect(["pkg:npm/a"])


async def test_collect_infers_each_gitbox_mirror_once_and_projects_metadata(monkeypatch) -> None:
    session = Session(
        [
            Response(
                [
                    {"purl": f"pkg:npm/{name}", "repository_url": "https://gitbox.apache.org/repos/asf/example.git"}
                    for name in "ab"
                ]
            ),
            Response(
                {
                    "archived": False,
                    "commit_stats": {"dds": "0.5"},
                    "metadata": {"files": {"security": "SECURITY.md", "code_of_conduct": None, "contributing": ""}},
                    "ignored": "x" * 10000,
                }
            ),
            Response({"active_maintainers": [{"name": "a"}, {"name": "b"}]}),
        ]
    )
    monkeypatch.setattr(observations.util, "create_secure_session", mock.Mock(return_value=session))
    result = await observations.collect(["pkg:npm/a", "pkg:npm/b"])
    assert len(session.calls) == 3
    assert result["pkg:npm/a"] == result["pkg:npm/b"]
    assert result["pkg:npm/a"]["inferred_mirror"] == "github.com/apache/example"
    assert result["pkg:npm/a"]["dds"] == 0.5
    assert result["pkg:npm/a"]["governance_files"] == 1
    assert result["pkg:npm/a"]["active_maintainers"] == 2
    assert "apache%2Fexample" in session.calls[1][0]


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        {"error": "bad"},
        [{"purl": "pkg:npm/unrequested"}],
        [{"purl": "pkg:npm/a"}, {"purl": "pkg:npm/a"}],
        [{"purl": "pkg:npm/a", "repo_metadata": []}],
        [{"purl": "pkg:npm/a", "latest_release_published_at": 20240101}],
        [{"purl": "pkg:npm/a", "repository_url": ["https://gitbox.apache.org/repos/asf/example.git"]}],
    ],
)
async def test_collect_malformed_or_uncorrelated_response_is_operational(monkeypatch, payload) -> None:
    session = Session([Response(payload)])
    monkeypatch.setattr(observations.util, "create_secure_session", mock.Mock(return_value=session))
    with pytest.raises((ValueError, streaming.MalformedError)):
        await observations.collect(["pkg:npm/a"])


async def test_collect_missing_mirror_is_unknown_but_outage_raises(monkeypatch) -> None:
    package = {"purl": "pkg:npm/a", "repository_url": "https://gitbox.apache.org/repos/asf/example.git"}
    session = Session([Response([package]), Response({}, 404), Response({}, 404)])
    monkeypatch.setattr(observations.util, "create_secure_session", mock.Mock(return_value=session))
    result = await observations.collect(["pkg:npm/a"])
    assert "archived" not in result["pkg:npm/a"]
    assert "active_maintainers" not in result["pkg:npm/a"]
    session.responses = [Response([package]), Response({}, 503)]
    with pytest.raises(aiohttp.ClientError):
        await observations.collect(["pkg:npm/a"])
