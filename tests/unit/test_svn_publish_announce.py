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

import pathlib
import unittest.mock as mock

import pytest

import atr.constants as constants
import atr.models.safe as safe
import atr.storage as storage
import atr.storage.writers.announce as announce_writer
import atr.util as util


async def fake_propagation_blocked(
    target: util.SvnPublishTarget,
    public_url: str,
    rel_paths: list[str],
) -> util.PropagationSummary:
    return _summary(target, public_url, rel_paths, 403, "HTTP 403")


async def fake_propagation_missing(
    target: util.SvnPublishTarget,
    public_url: str,
    rel_paths: list[str],
) -> util.PropagationSummary:
    return _summary(target, public_url, rel_paths, 404, "HTTP 404")


async def fake_propagation_unreachable(
    target: util.SvnPublishTarget,
    public_url: str,
    rel_paths: list[str],
) -> util.PropagationSummary:
    return _summary(target, public_url, rel_paths, None, "Cannot connect to host")


async def test_announce_acknowledgement_overrides_unreachable_server(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_text("content", encoding="utf-8")

    monkeypatch.setattr(announce_writer.util, "check_propagation", fake_propagation_unreachable)
    warnings: list[str] = []
    monkeypatch.setattr(announce_writer.log, "warning", warnings.append)
    writer = object.__new__(announce_writer.ReleaseManager)
    check = getattr(writer, "_ReleaseManager__check_publication_artifacts")
    public_url = f"{constants.DOWNLOADS_APACHE_URL}/project"

    await check(safe.StatePath(tmp_path), util.SvnPublishTarget.RELEASE, public_url, True)

    assert any("unreachable" in warning for warning in warnings)


async def test_announce_blocks_when_artifact_missing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_text("content", encoding="utf-8")

    monkeypatch.setattr(announce_writer.util, "check_propagation", fake_propagation_missing)
    writer = object.__new__(announce_writer.ReleaseManager)
    check = getattr(writer, "_ReleaseManager__check_publication_artifacts")
    public_url = f"{constants.DOWNLOADS_APACHE_URL}/project"

    with pytest.raises(storage.AccessError) as info:
        await check(safe.StatePath(tmp_path), util.SvnPublishTarget.RELEASE, public_url, True)

    assert info.value.status == 409
    assert "artifact.tar.gz" in str(info.value)


async def test_announce_blocks_when_artifact_not_public(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_text("content", encoding="utf-8")

    monkeypatch.setattr(announce_writer.util, "check_propagation", fake_propagation_blocked)
    writer = object.__new__(announce_writer.ReleaseManager)
    check = getattr(writer, "_ReleaseManager__check_publication_artifacts")
    public_url = f"{constants.DOWNLOADS_APACHE_URL}/project"

    with pytest.raises(storage.AccessError) as info:
        await check(safe.StatePath(tmp_path), util.SvnPublishTarget.RELEASE, public_url, True)

    assert not isinstance(info.value, storage.PropagationUnreachableError)
    assert info.value.status == 409
    assert "HTTP 403" in str(info.value)


async def test_announce_blocks_when_server_unreachable(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_text("content", encoding="utf-8")

    monkeypatch.setattr(announce_writer.util, "check_propagation", fake_propagation_unreachable)
    writer = object.__new__(announce_writer.ReleaseManager)
    check = getattr(writer, "_ReleaseManager__check_publication_artifacts")
    public_url = f"{constants.DOWNLOADS_APACHE_URL}/project"

    with pytest.raises(storage.PropagationUnreachableError) as info:
        await check(safe.StatePath(tmp_path), util.SvnPublishTarget.RELEASE, public_url, False)

    assert info.value.status == 503
    assert "see https://status.apache.org/ for its status." in str(info.value)


async def test_announce_local_blocks_when_artifact_missing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_text("content", encoding="utf-8")

    monkeypatch.setattr(announce_writer.svn, "list_files", mock.AsyncMock(return_value=["other.tar.gz"]))
    writer = object.__new__(announce_writer.ReleaseManager)
    check = getattr(writer, "_ReleaseManager__check_local_publication_artifacts")

    with pytest.raises(storage.AccessError) as info:
        await check(safe.StatePath(tmp_path), "svn://127.0.0.1:3690/atr-dev-publish/tooling")

    assert info.value.status == 409
    assert "artifact.tar.gz" in str(info.value)


async def test_announce_local_passes_when_artifacts_listed(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_text("content", encoding="utf-8")

    monkeypatch.setattr(announce_writer.svn, "list_files", mock.AsyncMock(return_value=["artifact.tar.gz"]))
    writer = object.__new__(announce_writer.ReleaseManager)
    check = getattr(writer, "_ReleaseManager__check_local_publication_artifacts")

    await check(safe.StatePath(tmp_path), "svn://127.0.0.1:3690/atr-dev-publish/tooling")


def _summary(
    target: util.SvnPublishTarget,
    public_url: str,
    rel_paths: list[str],
    status: int | None,
    error: str,
) -> util.PropagationSummary:
    outcomes = [
        util.PropagationOutcome(
            rel_path=rel_path,
            public_url=f"{public_url}/{rel_path}",
            ok=False,
            status=status,
            error=error,
        )
        for rel_path in rel_paths
    ]
    return util.PropagationSummary(target=target, total=len(outcomes), reachable=0, outcomes=outcomes)
