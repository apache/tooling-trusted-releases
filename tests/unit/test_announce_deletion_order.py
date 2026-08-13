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

import atr.models.args as args
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.announce as announce


def added_tasks(release_manager: announce.ReleaseManager, task_type: sql.TaskType) -> list[sql.Task]:
    data = getattr(release_manager, "_ReleaseManager__data")
    added = [call.args[0] for call in data.add.call_args_list]
    return [task for task in added if getattr(task, "task_type", None) == task_type]


def announcing_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, calls: list[str], finalise: bool = False
) -> announce.ReleaseManager:
    unfinished = tmp_path / "unfinished" / "example" / "2.0.0"
    (unfinished / "00003").mkdir(parents=True)
    monkeypatch.setattr(announce.config.get(), "RELEASE_FINALISE", finalise)
    monkeypatch.setattr(announce.aioshutil, "move", mock.AsyncMock(side_effect=lambda *a, **k: calls.append("move")))
    monkeypatch.setattr(announce.config, "is_dev_environment", lambda: True)
    monkeypatch.setattr(announce.config, "svn_publish_kind", lambda: announce.config.SvnPublishKind.ASF_DISTRIBUTION)
    monkeypatch.setattr(
        announce.interaction,
        "release_completed_svn_publish_task_for_revision",
        mock.AsyncMock(return_value=SimpleNamespace(task_args={}, result=None)),
    )
    monkeypatch.setattr(
        announce.construct, "announce_release_subject_and_body", mock.AsyncMock(return_value=("Subject", ""))
    )
    monkeypatch.setattr(
        announce.construct, "announce_release_subject_default", mock.AsyncMock(return_value="Subject template")
    )
    monkeypatch.setattr(
        announce.construct, "release_notification", lambda *a, **k: SimpleNamespace(as_task_args=lambda: {})
    )
    monkeypatch.setattr(announce.log, "audit_flush", lambda: calls.append("flush"))
    monkeypatch.setattr(announce.paths, "release_directory", lambda _release: tmp_path / "finished" / "example")
    monkeypatch.setattr(announce.paths, "release_directory_base", lambda _release: unfinished)
    monkeypatch.setattr(announce.util, "chmod_directories", lambda *a, **k: calls.append("chmod"))
    monkeypatch.setattr(
        announce.util,
        "delete_immutable_directory",
        mock.AsyncMock(side_effect=lambda *a, **k: calls.append("delete")),
    )
    monkeypatch.setattr(announce.util, "permitted_announce_recipients", lambda *a, **k: ["announce@example.apache.org"])
    monkeypatch.setattr(announce.util, "publication_check_url", lambda *a, **k: "https://example.invalid/")
    monkeypatch.setattr(announce.util, "svn_publish_internal_url", lambda *a, **k: "https://example.invalid/")
    monkeypatch.setattr(announce.util, "svn_publish_target", lambda: None)
    data = mock.MagicMock()
    data.release.return_value.demand = mock.AsyncMock(return_value=release_row())
    # A released version queues a catalog-site regeneration, which first looks for an existing queued one.
    data.task.return_value.get = mock.AsyncMock(return_value=None)
    data.add = mock.MagicMock(
        side_effect=lambda obj: (
            calls.append("finalise") if getattr(obj, "task_type", None) == sql.TaskType.RELEASE_FINALISE else None
        )
    )
    data.commit = mock.AsyncMock(side_effect=lambda: calls.append("commit"))
    release_manager = object.__new__(announce.ReleaseManager)
    release_manager._ReleaseManager__data = data
    release_manager._ReleaseManager__write = mock.MagicMock()
    release_manager._ReleaseManager__write_as = mock.MagicMock()
    release_manager._ReleaseManager__asf_uid = "alice"
    release_manager._ReleaseManager__committee_key = "alpha"
    release_manager._ReleaseManager__check_publication_artifacts = mock.AsyncMock()
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
        "fullname": "Alice",
    }


def release_row() -> SimpleNamespace:
    project = SimpleNamespace(
        committee_key="alpha",
        committee=SimpleNamespace(key="alpha"),
        is_active=True,
        key="example",
        policy_download_path_suffix="",
        release_policy=None,
    )
    return SimpleNamespace(
        cycle_key="example-default",
        key="example-2.0.0",
        project=project,
        project_key="example",
        release_policy=None,
        download_path_suffix=None,
        safe_latest_revision_number=safe.RevisionNumber("00003"),
        safe_version_key=safe.VersionKey("2.0.0"),
        unwrap_revision_number="00003",
        version="2.0.0",
    )


@pytest.mark.asyncio
async def test_announce_blocked_when_mail_relay_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    calls: list[str] = []
    release_manager = announcing_writer(monkeypatch, tmp_path, calls)
    monkeypatch.setattr(announce.config, "is_dev_environment", lambda: False)
    monkeypatch.setattr(announce.mail, "relay_reachable", mock.AsyncMock(return_value=False))

    with pytest.raises(storage.AccessError, match="mail relay") as info:
        await release_manager.release(**release_arguments())

    assert info.value.status == 503
    assert calls == []


@pytest.mark.asyncio
async def test_commit_failure_skips_prior_revision_deletion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    calls: list[str] = []
    release_manager = announcing_writer(monkeypatch, tmp_path, calls)
    release_manager._ReleaseManager__data.commit = mock.AsyncMock(side_effect=RuntimeError("error"))

    with pytest.raises(storage.AccessError, match="Files moved successfully"):
        await release_manager.release(**release_arguments())

    assert calls == ["chmod", "move"]


@pytest.mark.asyncio
async def test_finalise_queues_task_and_keeps_files(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    calls: list[str] = []
    release_manager = announcing_writer(monkeypatch, tmp_path, calls, finalise=True)
    monkeypatch.setattr(
        announce.interaction,
        "release_completed_svn_publish_task_for_revision",
        mock.AsyncMock(return_value=SimpleNamespace(task_args={}, result={"svn_revision": 85})),
    )

    await release_manager.release(**release_arguments())

    assert calls == ["flush", "finalise", "commit"]
    tasks = added_tasks(release_manager, sql.TaskType.RELEASE_FINALISE)
    assert len(tasks) == 1
    task_args = tasks[0].task_args
    parsed = args.ReleaseFinalise.model_validate(task_args)
    assert parsed.svn_revision == 85
    audit_call = release_manager._ReleaseManager__write_as.append_to_audit_log.call_args
    assert audit_call.kwargs["action"] == "release_announce"
    assert audit_call.kwargs["datetime"] == parsed.audit_until
    rows_path = release_manager._ReleaseManager__write_artifact_rows.call_args.args[2]
    assert pathlib.Path(str(rows_path)) == tmp_path / "unfinished" / "example" / "2.0.0" / "00003"
    assert (tmp_path / "unfinished" / "example" / "2.0.0" / "00003").is_dir()


@pytest.mark.asyncio
async def test_prior_revisions_deleted_after_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    calls: list[str] = []
    release_manager = announcing_writer(monkeypatch, tmp_path, calls)

    await release_manager.release(**release_arguments())

    assert calls == ["chmod", "move", "commit", "delete"]
    assert added_tasks(release_manager, sql.TaskType.RELEASE_FINALISE) == []
    audit_call = release_manager._ReleaseManager__write_as.append_to_audit_log.call_args
    assert audit_call.kwargs["action"] == "release_announce"
