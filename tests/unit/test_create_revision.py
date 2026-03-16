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

import os
import pathlib
import unittest.mock as mock

import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.types as types
import atr.storage.writers.revision as revision


class AsyncContextManager:
    async def __aenter__(self):
        return None

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


class FakeRevision:
    def __init__(
        self,
        release_name: str,
        release: object,
        asfuid: str,
        created: object,
        phase: sql.ReleasePhase,
        description: str | None,
        merge_base_revision_name: str | None = None,
        was_quarantined: bool = False,
    ):
        self.asfuid = asfuid
        self.created = created
        self.description = description
        self.merge_base_revision_name = merge_base_revision_name
        self.name = ""
        self.number = ""
        self.parent_name: str | None = None
        self.phase = phase
        self.release = release
        self.release_name = release_name
        self.was_quarantined = was_quarantined

    @property
    def safe_number(self) -> safe.RevisionNumber:
        return safe.RevisionNumber(self.number)


class MockSafeData:
    def __init__(self, parent_name: str, new_number: str = "00006"):
        self._new_revision: FakeRevision | None = None
        self._new_number = new_number
        self._parent_name = parent_name
        self.add = mock.MagicMock(side_effect=self._add)
        self.begin = mock.MagicMock(return_value=AsyncContextManager())
        self.begin_immediate = mock.AsyncMock()
        self.commit = mock.AsyncMock()
        self.flush = mock.AsyncMock(side_effect=self._flush)
        self.merge = mock.AsyncMock(side_effect=self._merge)
        self.refresh = mock.AsyncMock()

    def _add(self, new_revision: "FakeRevision") -> None:
        self._new_revision = new_revision

    async def _flush(self) -> None:
        if self._new_revision is None:
            raise RuntimeError("Expected data.add to set _new_revision before flush")
        self._new_revision.name = f"{self._new_revision.release_name} {self._new_number}"
        self._new_revision.number = self._new_number
        self._new_revision.parent_name = self._parent_name

    async def _merge(self, obj: object) -> object:
        return obj


class MockSafeSession:
    def __init__(self, data: MockSafeData):
        self._data = data

    async def __aenter__(self) -> MockSafeData:
        return self._data

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


@pytest.mark.asyncio
async def test_clone_from_older_revision_skips_merge_without_intervening_change(tmp_path: pathlib.Path):
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_PREVIEW
    release.project = mock.MagicMock()
    release.project.release_policy = None
    release.release_policy = None
    release_name = sql.release_name("proj", "1.0")

    latest_revision = mock.MagicMock()
    latest_revision.name = f"{release_name} 00005"
    latest_revision.number = "00005"
    latest_revision.safe_number = safe.RevisionNumber("00005")

    selected_revision = mock.MagicMock()
    selected_revision.name = f"{release_name} 00002"
    selected_revision.number = "00002"
    selected_revision.safe_number = safe.RevisionNumber("00002")

    mock_session = _mock_db_session(release, selected_revision=selected_revision)
    participant = _make_participant()
    safe_data = MockSafeData(parent_name=latest_revision.name)
    merge_mock = mock.AsyncMock()

    with (
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(
            revision.attestable, "load", new_callable=mock.AsyncMock, return_value=mock.MagicMock(paths={})
        ),
        mock.patch.object(
            revision.attestable, "paths_to_hashes_and_sizes", new_callable=mock.AsyncMock, return_value=({}, {})
        ),
        mock.patch.object(revision.attestable, "write_files_data", new_callable=mock.AsyncMock),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "detect_archives_requiring_quarantine", return_value=[]),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(
            revision.interaction, "latest_revision", new_callable=mock.AsyncMock, return_value=latest_revision
        ),
        mock.patch.object(revision.merge, "merge", new=merge_mock),
        mock.patch.object(revision.sql, "Revision", side_effect=_make_fake_revision),
        mock.patch.object(revision, "SafeSession", return_value=MockSafeSession(safe_data)),
        mock.patch.object(revision.tasks, "draft_checks", new_callable=mock.AsyncMock),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(
            revision.util, "create_hard_link_clone", new_callable=mock.AsyncMock
        ) as create_hard_link_clone_mock,
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=tmp_path),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}) as paths_to_inodes_mock,
        mock.patch.object(revision.paths, "release_directory", return_value=tmp_path / "releases" / "00006"),
        mock.patch.object(revision.paths, "release_directory_base", return_value=tmp_path / "releases"),
    ):
        await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"), safe.VersionKey("1.0"), "test", clone_from=safe.RevisionNumber("00002")
        )

    if merge_mock.called:
        raise AssertionError(
            "Expected create_revision(clone_from=...) to skip merge when no concurrent revision exists"
        )
    if create_hard_link_clone_mock.await_count != 1:
        raise AssertionError("Expected one hard-link clone from clone_from source revision")
    if paths_to_inodes_mock.call_count != 1:
        raise AssertionError("Expected one paths_to_inodes call when clone_from disables merge")

    clone_await_args = create_hard_link_clone_mock.await_args
    inodes_call_args = paths_to_inodes_mock.call_args
    if clone_await_args is None:
        raise AssertionError("Expected create_hard_link_clone await args")
    if inodes_call_args is None:
        raise AssertionError("Expected paths_to_inodes call args")

    clone_source = clone_await_args.args[0]
    clone_destination = clone_await_args.args[1]
    clone_kwargs = clone_await_args.kwargs
    inodes_input = inodes_call_args.args[0]
    expected_clone_source = tmp_path / "releases" / "00002"

    if clone_source != expected_clone_source:
        raise AssertionError("Expected hard-link clone source to match clone_from revision directory")
    if clone_kwargs.get("do_not_create_dest_dir") is not True:
        raise AssertionError("Expected hard-link clone to use do_not_create_dest_dir=True")
    if clone_destination != inodes_input:
        raise AssertionError("Expected inode scan to run only for the temporary working directory")


@pytest.mark.asyncio
async def test_intervening_revision_triggers_merge_and_uses_latest_parent(tmp_path: pathlib.Path):
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_PREVIEW
    release.project = mock.MagicMock()
    release.project.release_policy = None
    release.release_policy = None
    release_name = sql.release_name("proj", "1.0")

    old_revision = mock.MagicMock()
    old_revision.name = f"{release_name} 00005"
    old_revision.number = "00005"
    old_revision.safe_number = safe.RevisionNumber("00005")

    intervening_revision = mock.MagicMock()
    intervening_revision.name = f"{release_name} 00006"
    intervening_revision.number = "00006"
    intervening_revision.safe_number = safe.RevisionNumber("00006")

    first_attestable = mock.MagicMock(paths={"dist/a.tar.gz": "h1"})
    second_attestable = mock.MagicMock(paths={"dist/b.tar.gz": "h2"})

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    safe_data = MockSafeData(parent_name=f"{release_name} 00006", new_number="00007")
    merge_mock = mock.AsyncMock()
    load_mock = mock.AsyncMock(side_effect=[first_attestable, second_attestable])

    with (
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(revision.attestable, "load", new=load_mock),
        mock.patch.object(
            revision.attestable, "paths_to_hashes_and_sizes", new_callable=mock.AsyncMock, return_value=({}, {})
        ),
        mock.patch.object(revision.attestable, "write_files_data", new_callable=mock.AsyncMock),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "detect_archives_requiring_quarantine", return_value=[]),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(
            revision.interaction,
            "latest_revision",
            new_callable=mock.AsyncMock,
            side_effect=[old_revision, intervening_revision],
        ),
        mock.patch.object(revision.merge, "merge", new=merge_mock),
        mock.patch.object(revision.sql, "Revision", side_effect=_make_fake_revision),
        mock.patch.object(revision, "SafeSession", return_value=MockSafeSession(safe_data)),
        mock.patch.object(revision.tasks, "draft_checks", new_callable=mock.AsyncMock),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "create_hard_link_clone", new_callable=mock.AsyncMock),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=tmp_path),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(revision.paths, "release_directory", return_value=tmp_path / "releases" / "00007"),
        mock.patch.object(revision.paths, "release_directory_base", return_value=tmp_path / "releases"),
    ):
        created_revision = await participant.create_revision_with_quarantine("proj", "1.0", "test")

    assert isinstance(created_revision, FakeRevision)
    assert merge_mock.await_count == 1
    assert load_mock.await_count == 2

    merge_await_args = merge_mock.await_args
    assert merge_await_args is not None
    assert merge_await_args.args[5] == safe.RevisionNumber("00006")


@pytest.mark.asyncio
async def test_modify_failed_error_propagates_and_cleans_up(tmp_path: pathlib.Path):
    received_args: dict[str, object] = {}

    async def modify(path: pathlib.Path, old_rev: object) -> None:
        received_args["path"] = path
        received_args["old_rev"] = old_rev
        (path / "file.txt").write_text("Should be cleaned up.")
        raise types.FailedError("Intentional error")

    mock_session = _mock_db_session(mock.MagicMock())
    participant = _make_participant()

    with (
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.interaction, "latest_revision", new_callable=mock.AsyncMock, return_value=None),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=tmp_path),
    ):
        with pytest.raises(types.FailedError, match="Intentional error"):
            await participant.create_revision_with_quarantine("proj", "1.0", "test", modify=modify)

    assert isinstance(received_args["path"], pathlib.Path)
    assert received_args["old_rev"] is None
    assert not os.listdir(tmp_path)


def _make_fake_revision(**kwargs) -> FakeRevision:
    return FakeRevision(**kwargs)


def _make_participant() -> revision.CommitteeParticipant:
    mock_write = mock.MagicMock()
    mock_write.authorisation.asf_uid = "test"
    return revision.CommitteeParticipant(mock_write, mock.MagicMock(), mock.MagicMock(), "test")


def _mock_db_session(release: mock.MagicMock, selected_revision: mock.MagicMock | None = None) -> mock.MagicMock:
    mock_query = mock.MagicMock()
    mock_query.demand = mock.AsyncMock(return_value=release)
    mock_selected_query = mock.MagicMock()
    mock_selected_query.demand = mock.AsyncMock(return_value=selected_revision)
    mock_data = mock.AsyncMock()
    mock_data.release = mock.MagicMock(return_value=mock_query)
    mock_data.revision = mock.MagicMock(return_value=mock_selected_query)
    mock_data.__aenter__ = mock.AsyncMock(return_value=mock_data)
    mock_data.__aexit__ = mock.AsyncMock(return_value=False)
    return mock_data
