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
from types import SimpleNamespace
from typing import Any

import pytest

import atr.models.safe as safe
import atr.storage as storage
import atr.storage.writers.announce as announce


def announcing_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, calls: list[str]
) -> announce.ReleaseManager:
    unfinished = tmp_path / "unfinished" / "example" / "2.0.0"
    (unfinished / "00003").mkdir(parents=True)
    monkeypatch.setattr(announce.aioshutil, "move", mock.AsyncMock(side_effect=lambda *a, **k: calls.append("move")))
    monkeypatch.setattr(announce.config, "get", lambda: SimpleNamespace(SVN_PUBLISH_URL=""))
    monkeypatch.setattr(
        announce.construct, "announce_release_subject_and_body", mock.AsyncMock(return_value=("Subject", ""))
    )
    monkeypatch.setattr(
        announce.construct, "announce_release_subject_default", mock.AsyncMock(return_value="Subject template")
    )
    monkeypatch.setattr(
        announce.construct, "release_notification", lambda *a, **k: SimpleNamespace(as_task_args=lambda: {})
    )
    monkeypatch.setattr(announce.paths, "release_directory", lambda _release: tmp_path / "finished" / "example")
    monkeypatch.setattr(announce.paths, "release_directory_base", lambda _release: unfinished)
    monkeypatch.setattr(announce.util, "chmod_directories", lambda *a, **k: None)
    monkeypatch.setattr(
        announce.util,
        "delete_immutable_directory",
        mock.AsyncMock(side_effect=lambda *a, **k: calls.append("delete")),
    )
    monkeypatch.setattr(announce.util, "permitted_announce_recipients", lambda *a, **k: ["announce@example.apache.org"])
    data = mock.MagicMock()
    data.release.return_value.demand = mock.AsyncMock(return_value=release_row())
    data.commit = mock.AsyncMock(side_effect=lambda: calls.append("commit"))
    release_manager = object.__new__(announce.ReleaseManager)
    release_manager._ReleaseManager__data = data
    release_manager._ReleaseManager__write = mock.MagicMock()
    release_manager._ReleaseManager__write_as = mock.MagicMock()
    release_manager._ReleaseManager__asf_uid = "alice"
    release_manager._ReleaseManager__committee_key = "alpha"
    release_manager._ReleaseManager__promote_in_database = mock.AsyncMock()
    release_manager._ReleaseManager__write_artifact_rows = mock.AsyncMock()
    return release_manager


def release_arguments() -> dict[str, Any]:
    return {
        "project_key": safe.ProjectKey("example"),
        "version_key": safe.VersionKey("2.0.0"),
        "preview_revision_number": safe.RevisionNumber("00003"),
        "email_to": "announce@example.apache.org",
        "body": "Body",
        "download_path_suffix": None,
        "fullname": "Alice",
    }


def release_row() -> SimpleNamespace:
    project = SimpleNamespace(
        committee_key="alpha",
        committee=SimpleNamespace(key="alpha"),
        is_active=True,
        key="example",
        release_policy=None,
    )
    return SimpleNamespace(
        cycle_key="example-default",
        key="example-2.0.0",
        project=project,
        project_key="example",
        release_policy=None,
        safe_latest_revision_number=safe.RevisionNumber("00003"),
        unwrap_revision_number="00003",
        version="2.0.0",
    )


@pytest.mark.asyncio
async def test_commit_failure_skips_prior_revision_deletion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    calls: list[str] = []
    release_manager = announcing_writer(monkeypatch, tmp_path, calls)
    release_manager._ReleaseManager__data.commit = mock.AsyncMock(side_effect=RuntimeError("error"))

    with pytest.raises(storage.AccessError, match="Files moved successfully"):
        await release_manager.release(**release_arguments())

    assert calls == ["move"]


@pytest.mark.asyncio
async def test_prior_revisions_deleted_after_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    calls: list[str] = []
    release_manager = announcing_writer(monkeypatch, tmp_path, calls)

    await release_manager.release(**release_arguments())

    assert calls == ["move", "commit", "delete"]
