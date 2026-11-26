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

import types
import unittest.mock as mock

import pytest

import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.announce as announce_writer
import atr.tasks.announce as announce
import atr.tasks.task as task

_ARGS = {
    "asf_uid": "alice",
    "fullname": "Alice Example",
    "project_key": "example",
    "version_key": "1.0.0",
    "revision_number": "00002",
    "email_to": "announce@example.apache.org",
    "body": "Released.",
    "subject_template_hash": "abc",
}
_PROJECT = safe.ProjectKey("example")
_REVISION = safe.RevisionNumber("00002")
_VERSION = safe.VersionKey("1.0.0")


async def test_queue_adds_a_task(monkeypatch: pytest.MonkeyPatch) -> None:
    writer, data = _writer(monkeypatch)

    await _queue(writer)

    (queued,) = data.add.call_args.args
    assert (queued.task_type, queued.asf_uid, queued.revision_number) == (
        sql.TaskType.ANNOUNCE_WHEN_READY,
        "alice",
        "00002",
    )
    assert queued.task_args["email_to"] == "announce@example.apache.org"


async def test_queue_cancel_refuses_when_nothing_is_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    writer, data = _writer(monkeypatch)
    data.execute_query = mock.AsyncMock(return_value=types.SimpleNamespace(rowcount=0))

    with pytest.raises(storage.AccessError):
        await writer.queue_cancel(_PROJECT, _VERSION)


@pytest.mark.parametrize("refusal", ["expedited", "older_revision", "already_queued"])
async def test_queue_refusals(monkeypatch: pytest.MonkeyPatch, refusal: str) -> None:
    writer, data = _writer(
        monkeypatch,
        expedited=refusal == "expedited",
        latest="00003" if (refusal == "older_revision") else "00002",
        already_queued=refusal == "already_queued",
    )

    with pytest.raises(storage.AccessError):
        await _queue(writer)
    data.add.assert_not_called()


async def test_queue_refuses_when_another_was_queued_at_the_same_time(monkeypatch: pytest.MonkeyPatch) -> None:
    writer, data = _writer(monkeypatch)
    data.task.return_value.all = mock.AsyncMock(return_value=[object(), object()])

    with pytest.raises(storage.AccessError):
        await _queue(writer)
    data.rollback.assert_awaited_once()
    data.commit.assert_not_awaited()


async def test_when_ready_ends_quietly_when_already_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    notified = _patch_task(monkeypatch, release=None, announced=True)

    result = await announce.when_ready(_ARGS)

    assert not result.announced
    notified.assert_not_awaited()


async def test_when_ready_gives_up_when_the_release_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    notified = _patch_task(monkeypatch, release=None)

    result = await announce.when_ready(_ARGS)

    assert not result.announced
    assert notified.await_args.args[2] is sql.NotificationLevel.ERROR


@pytest.mark.parametrize(
    "refusal", [storage.AccessError("A newer revision appeared"), RuntimeError("A newer revision appeared")]
)
async def test_when_ready_gives_up_when_the_writer_fails(monkeypatch: pytest.MonkeyPatch, refusal: Exception) -> None:
    notified = _patch_task(monkeypatch, refusal=refusal)

    result = await announce.when_ready(_ARGS)

    assert (result.announced, "A newer revision appeared" in result.message) == (False, True)
    assert notified.await_args.args[2] is sql.NotificationLevel.ERROR


async def test_when_ready_sends_once_nothing_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    notified = _patch_task(monkeypatch)

    result = await announce.when_ready(_ARGS)

    assert result.announced
    assert notified.await_args.args[2] is sql.NotificationLevel.INFO


@pytest.mark.parametrize("unreachable", [False, True])
async def test_when_ready_waits(monkeypatch: pytest.MonkeyPatch, unreachable: bool) -> None:
    refusal = storage.PropagationUnreachableError("Download server unreachable") if unreachable else None
    notified = _patch_task(monkeypatch, blocked="" if unreachable else "Cannot announce yet.", refusal=refusal)

    with pytest.raises(task.DeferredError) as deferred:
        await announce.when_ready(_ARGS)

    assert isinstance(deferred.value.result, results.AnnounceWhenReady)
    notified.assert_not_awaited()


def _patch_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    release: object = True,
    blocked: str = "",
    refusal: Exception | None = None,
    announced: bool = False,
) -> mock.AsyncMock:
    def find_release(**kwargs: object) -> types.SimpleNamespace:
        if kwargs["phase"] is sql.ReleasePhase.RELEASE:
            return types.SimpleNamespace(get=mock.AsyncMock(return_value=True if announced else None))
        return types.SimpleNamespace(get=mock.AsyncMock(return_value=release))

    data = mock.MagicMock()
    data.release = find_release
    session = mock.AsyncMock()
    session.__aenter__.return_value = data
    monkeypatch.setattr("atr.db.session", lambda: session)
    monkeypatch.setattr("atr.shared.announce.blocked_message", mock.AsyncMock(return_value=blocked))
    warm = mock.MagicMock()
    warm.announce.release = mock.AsyncMock(side_effect=refusal)
    write = mock.MagicMock()
    write.as_project_release_manager = mock.AsyncMock(return_value=warm)
    writing = mock.AsyncMock()
    writing.__aenter__.return_value = write
    monkeypatch.setattr("atr.storage.write", lambda _asf_uid: writing)
    notified = mock.AsyncMock()
    monkeypatch.setattr("atr.notify.user", notified)
    return notified


async def _queue(writer: announce_writer.ReleaseManager) -> None:
    await writer.queue(
        _PROJECT, _VERSION, _REVISION, "announce@example.apache.org", "Released.", "Alice Example", "abc"
    )


def _writer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expedited: bool = False,
    latest: str = "00002",
    already_queued: bool = False,
) -> tuple[announce_writer.ReleaseManager, mock.MagicMock]:
    release = types.SimpleNamespace(
        expedited=expedited,
        safe_latest_revision_number=safe.RevisionNumber(latest),
        safe_version_key=_VERSION,
        project=types.SimpleNamespace(committee_key="example", committee=mock.MagicMock()),
    )
    data = mock.MagicMock(commit=mock.AsyncMock(), flush=mock.AsyncMock(), rollback=mock.AsyncMock())
    data.task.return_value.all = mock.AsyncMock(return_value=[object()])
    data.release.return_value.demand = mock.AsyncMock(return_value=release)
    data.execute_query = mock.AsyncMock(return_value=types.SimpleNamespace(rowcount=1))
    monkeypatch.setattr("atr.storage.ensure_project_active", lambda _project: None)
    monkeypatch.setattr(
        "atr.db.interaction.release_queued_announcement",
        mock.AsyncMock(return_value=mock.MagicMock() if already_queued else None),
    )
    monkeypatch.setattr(
        "atr.util.permitted_announce_recipients", lambda *_args, **_kwargs: ["announce@example.apache.org"]
    )
    monkeypatch.setattr("atr.construct.announce_release_subject_default", mock.AsyncMock(return_value="subject"))
    monkeypatch.setattr("atr.construct.template_hash", lambda _template: "abc")
    writer = object.__new__(announce_writer.ReleaseManager)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__write = mock.MagicMock()
    writer._ReleaseManager__write_as = mock.MagicMock()
    writer._ReleaseManager__asf_uid = "alice"
    writer._ReleaseManager__committee_key = "example"
    return writer, data
