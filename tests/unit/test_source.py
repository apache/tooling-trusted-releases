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

import unittest.mock as mock

import pytest

import atr.attestable as attestable
import atr.models.attestable
import atr.models.github as github
import atr.models.safe as safe
import atr.models.sql as sql
import atr.source as source


@pytest.mark.parametrize("override", [None, "c" * 40])
def test_advance_keeps_source_choice(override: str | None, github_payload: github.TrustedPublisherPayload):
    selected = atr.models.attestable.SourceV2(repository="apache/test", default="a" * 40, override=override)
    result = source.advance(selected, github_payload)
    assert result.default == github_payload.sha
    assert result.override == override
    assert result.sha == (override or github_payload.sha)
    assert selected.default == "a" * 40


async def test_initial_accepts_repository_configured_after_first_revision():
    release = sql.Release(
        project=sql.Project(key="test", name="Test", repositories=["https://github.com/apache/test"]), version="1.0"
    )
    previous = atr.models.attestable.AttestableV2(source=atr.models.attestable.SourceV2())
    selected = await source.initial(release, previous)
    assert selected.repository == "apache/test"
    assert previous.source.repository == ""


@pytest.mark.parametrize("recorded_hash", ["b" * 40, "c" * 40])
async def test_initial_preserves_legacy_choice(
    monkeypatch: pytest.MonkeyPatch, github_payload: github.TrustedPublisherPayload, recorded_hash: str
):
    release = sql.Release(
        project=sql.Project(key="test", name="Test"), project_key="test", version="1.0", commit_hash=recorded_hash
    )
    monkeypatch.setattr(attestable, "latest_github_tp_payload", mock.AsyncMock(return_value=github_payload))
    selected = await source.initial(release, None)
    assert selected.repository == github_payload.repository
    assert selected.default == github_payload.sha
    assert selected.sha == recorded_hash
    assert selected.override == (recorded_hash if (recorded_hash != github_payload.sha) else None)


def test_override_can_be_set_or_cleared():
    selected = atr.models.attestable.SourceV2(repository="apache/test", default="a" * 40, override="b" * 40)
    confirmed = source.advance(selected, None, "b" * 40)
    cleared = source.advance(selected, None, None)
    assert confirmed.sha == "b" * 40
    assert cleared.sha == "a" * 40
    assert cleared.override is None
    with pytest.raises(ValueError, match="Configure a GitHub repository"):
        source.advance(atr.models.attestable.SourceV2(), None, "b" * 40)


async def test_read_and_cache_inputs_use_requested_snapshot(
    monkeypatch: pytest.MonkeyPatch, github_payload: github.TrustedPublisherPayload
):
    pk, vk, rev = safe.ProjectKey("test"), safe.VersionKey("1.0"), safe.RevisionNumber("00001")
    selected = atr.models.attestable.SourceV2(repository="apache/test", default=github_payload.sha, override="c" * 40)
    monkeypatch.setattr(
        attestable, "load", mock.AsyncMock(return_value=atr.models.attestable.AttestableV2(source=selected))
    )
    original = mock.AsyncMock(return_value=github_payload)
    monkeypatch.setattr(attestable, "github_tp_payload_read", original)
    assert (await source.read(pk, vk, rev)).sha == "c" * 40
    assert await source.inputs(pk, vk, rev) == {"source_repository": "apache/test", "source_commit": "c" * 40}
    attestable.load.assert_awaited_with(pk, vk, rev)
    original.assert_not_awaited()
    monkeypatch.setattr(attestable, "load", mock.AsyncMock(return_value=None))
    assert (await source.read(pk, vk, rev)).sha == github_payload.sha
    original.assert_awaited_once_with(pk, vk, rev)


@pytest.mark.parametrize(
    ("repositories", "policy_name", "expected"),
    [
        (["https://github.com/apache/test.git"], "", "apache/test"),
        (["https://github.com/apache/first", "https://github.com/apache/second"], "", ""),
        (["https://github.com/apache/first"], "configured", "apache/configured"),
        ([], "", ""),
    ],
)
def test_repository_requires_one_choice(repositories: list[str], policy_name: str, expected: str):
    project = sql.Project(
        key="test",
        name="Test",
        repositories=repositories,
        release_policy=sql.ReleasePolicy(github_repository_name=policy_name),
    )
    assert source.repository(project) == expected
