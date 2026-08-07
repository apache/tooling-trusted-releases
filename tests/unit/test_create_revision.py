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

import datetime
import os
import pathlib
import unittest.mock as mock

import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.datatypes as datatypes
import atr.storage.writers.release as release
import atr.storage.writers.revision as revision


class AsyncContextManager:
    async def __aenter__(self):
        return None

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


class FakeRevision:
    def __init__(
        self,
        release_key: str,
        release: object,
        asfuid: str,
        created: object,
        phase: sql.ReleasePhase,
        description: str | None,
        merge_base_revision_key: str | None = None,
        was_quarantined: bool = False,
    ):
        self.asfuid = asfuid
        self.created = created
        self.description = description
        self.merge_base_revision_key = merge_base_revision_key
        self.key = ""
        self.number = ""
        self.parent_key: str | None = None
        self.seq: int = 0
        self.phase = phase
        self.release = release
        self.release_key = release_key
        self.was_quarantined = was_quarantined

    @property
    def safe_number(self) -> safe.RevisionNumber:
        return safe.RevisionNumber(self.number)


class MockSafeData:
    def __init__(self, parent_key: str, new_number: str = "00006"):
        self._new_revision: FakeRevision | None = None
        self._new_number = new_number
        self._parent_key = parent_key
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
        self._new_revision.key = f"{self._new_revision.release_key} {self._new_number}"
        self._new_revision.number = self._new_number
        self._new_revision.seq = int(self._new_number)
        self._new_revision.parent_key = self._parent_key

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
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_PREVIEW
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.release_policy = None
    release.release_policy = None
    release.activity_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    release_key = sql.release_key("proj", "1.0")
    release.key = release_key

    latest_revision = mock.MagicMock()
    latest_revision.key = f"{release_key} 00005"
    latest_revision.number = "00005"
    latest_revision.safe_number = safe.RevisionNumber("00005")

    selected_revision = mock.MagicMock()
    selected_revision.key = f"{release_key} 00002"
    selected_revision.number = "00002"
    selected_revision.safe_number = safe.RevisionNumber("00002")

    mock_session = _mock_db_session(release, selected_revision=selected_revision)
    participant = _make_participant()
    safe_data = MockSafeData(parent_key=latest_revision.key)
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
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}) as paths_to_inodes_mock,
        mock.patch.object(revision.paths, "release_directory", return_value=temp_dir / "releases" / "00006"),
        mock.patch.object(revision.paths, "release_directory_base", return_value=temp_dir / "releases"),
    ):
        await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_PREVIEW}),
            clone_from=safe.RevisionNumber("00002"),
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
    expected_clone_source = temp_dir / "releases" / "00002"

    if clone_source != expected_clone_source:
        raise AssertionError("Expected hard-link clone source to match clone_from revision directory")
    if clone_kwargs.get("do_not_create_dest_dir") is not True:
        raise AssertionError("Expected hard-link clone to use do_not_create_dest_dir=True")
    if clone_destination.path != inodes_input:
        raise AssertionError("Expected inode scan to run only for the temporary working directory")


@pytest.mark.asyncio
async def test_expected_revision_match_proceeds(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.release_policy = None
    release.release_policy = None
    release.activity_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    release_key = sql.release_key("proj", "1.0")
    release.key = release_key

    old_revision = mock.MagicMock()
    old_revision.key = f"{release_key} 00005"
    old_revision.number = "00005"
    old_revision.safe_number = safe.RevisionNumber("00005")

    first_attestable = mock.MagicMock(paths={"dist/a.tar.gz": "h1"})

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    safe_data = MockSafeData(parent_key=f"{release_key} 00005", new_number="00006")
    merge_mock = mock.AsyncMock()

    with (
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(revision.attestable, "load", new_callable=mock.AsyncMock, return_value=first_attestable),
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
            side_effect=[old_revision, old_revision],
        ),
        mock.patch.object(revision.merge, "merge", new=merge_mock),
        mock.patch.object(revision.sql, "Revision", side_effect=_make_fake_revision),
        mock.patch.object(revision, "SafeSession", return_value=MockSafeSession(safe_data)),
        mock.patch.object(revision.tasks, "draft_checks", new_callable=mock.AsyncMock),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "create_hard_link_clone", new_callable=mock.AsyncMock),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(revision.paths, "release_directory", return_value=temp_dir / "releases" / "00006"),
        mock.patch.object(revision.paths, "release_directory_base", return_value=temp_dir / "releases"),
    ):
        created_revision = await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            expected_revision=safe.RevisionNumber("00005"),
        )

    assert isinstance(created_revision, FakeRevision)
    assert merge_mock.await_count == 0


@pytest.mark.asyncio
async def test_expected_revision_mismatch_rejected(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.release_policy = None
    release.release_policy = None
    release.activity_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    release_key = sql.release_key("proj", "1.0")
    release.key = release_key

    old_revision = mock.MagicMock()
    old_revision.key = f"{release_key} 00005"
    old_revision.number = "00005"
    old_revision.safe_number = safe.RevisionNumber("00005")

    intervening_revision = mock.MagicMock()
    intervening_revision.key = f"{release_key} 00006"
    intervening_revision.number = "00006"
    intervening_revision.seq = 6
    intervening_revision.safe_number = safe.RevisionNumber("00006")

    first_attestable = mock.MagicMock(paths={"dist/a.tar.gz": "h1"})
    second_attestable = mock.MagicMock(paths={"dist/b.tar.gz": "h2"})

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    safe_data = MockSafeData(parent_key=f"{release_key} 00006", new_number="00007")
    merge_mock = mock.AsyncMock()
    load_mock = mock.AsyncMock(side_effect=[first_attestable, second_attestable])

    with (
        mock.patch.object(revision.attestable, "load", new=load_mock),
        mock.patch.object(
            revision.attestable, "paths_to_hashes_and_sizes", new_callable=mock.AsyncMock, return_value=({}, {})
        ),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(
            revision.interaction,
            "latest_revision",
            new_callable=mock.AsyncMock,
            side_effect=[old_revision, intervening_revision],
        ),
        mock.patch.object(revision.merge, "merge", new=merge_mock),
        mock.patch.object(revision, "SafeSession", return_value=MockSafeSession(safe_data)),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "create_hard_link_clone", new_callable=mock.AsyncMock),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(revision.paths, "release_directory", return_value=temp_dir / "releases" / "00007"),
        mock.patch.object(revision.paths, "release_directory_base", return_value=temp_dir / "releases"),
        pytest.raises(datatypes.RevisionMismatchError, match="no longer the latest"),
    ):
        await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            expected_revision=safe.RevisionNumber("00005"),
        )

    assert not (tmp_path / "releases" / "00007").exists()


@pytest.mark.asyncio
async def test_intervening_revision_triggers_merge_and_uses_latest_parent(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_PREVIEW
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.release_policy = None
    release.release_policy = None
    release.activity_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    release_key = sql.release_key("proj", "1.0")
    release.key = release_key

    old_revision = mock.MagicMock()
    old_revision.key = f"{release_key} 00005"
    old_revision.number = "00005"
    old_revision.safe_number = safe.RevisionNumber("00005")

    intervening_revision = mock.MagicMock()
    intervening_revision.key = f"{release_key} 00006"
    intervening_revision.number = "00006"
    intervening_revision.seq = 6
    intervening_revision.safe_number = safe.RevisionNumber("00006")

    first_attestable = mock.MagicMock(paths={"dist/a.tar.gz": "h1"})
    second_attestable = mock.MagicMock(paths={"dist/b.tar.gz": "h2"})

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    safe_data = MockSafeData(parent_key=f"{release_key} 00006", new_number="00007")
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
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(revision.paths, "release_directory", return_value=temp_dir / "releases" / "00007"),
        mock.patch.object(revision.paths, "release_directory_base", return_value=temp_dir / "releases"),
    ):
        created_revision = await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_PREVIEW}),
        )

    assert isinstance(created_revision, FakeRevision)
    assert merge_mock.await_count == 1
    assert load_mock.await_count == 2

    merge_await_args = merge_mock.await_args
    assert merge_await_args is not None
    assert merge_await_args.args[6] == 6


@pytest.mark.asyncio
async def test_intervening_revision_with_path_provenance_verifies_sources(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.release_policy = None
    release.release_policy = None
    release.activity_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    release_key = sql.release_key("proj", "1.0")
    release.key = release_key

    old_revision = mock.MagicMock()
    old_revision.key = f"{release_key} 00005"
    old_revision.number = "00005"
    old_revision.safe_number = safe.RevisionNumber("00005")

    intervening_revision = mock.MagicMock()
    intervening_revision.key = f"{release_key} 00006"
    intervening_revision.number = "00006"
    intervening_revision.seq = 6
    intervening_revision.safe_number = safe.RevisionNumber("00006")

    import contextlib

    import atr.models.attestable as attestable

    provenance = attestable.ProvenanceV2(
        generator=attestable.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "initiated_by": "alice",
            "signature_path": "example.tar.gz.asc",
            "source_paths": ["example.tar.gz"],
        },
    )
    provenance_map = {safe.RelPath("example.tar.gz.sha512"): provenance}

    async def modify(path: safe.StatePath, old_rev: object) -> dict[safe.RelPath, attestable.ProvenanceV2] | None:
        return provenance_map

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    safe_data = MockSafeData(parent_key=f"{release_key} 00006", new_number="00007")
    verify_mock = mock.AsyncMock()

    patches = [
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(revision.attestable, "load", new_callable=mock.AsyncMock, return_value=mock.MagicMock()),
        mock.patch.object(
            revision.attestable,
            "paths_to_hashes_and_sizes",
            new_callable=mock.AsyncMock,
            return_value=(
                {
                    safe.RelPath("example.tar.gz"): "blake3:src",
                    safe.RelPath("example.tar.gz.sha512"): "blake3:hash",
                },
                {
                    safe.RelPath("example.tar.gz"): 100,
                    safe.RelPath("example.tar.gz.sha512"): 64,
                },
            ),
        ),
        mock.patch.object(revision.attestable, "write_files_data", new_callable=mock.AsyncMock),
        mock.patch.object(revision.attestable, "compute_file_state_rows", return_value=[]),
        mock.patch.object(
            revision.attestable,
            "compute_classifications",
            new_callable=mock.AsyncMock,
            return_value={
                safe.RelPath("example.tar.gz"): "source",
                safe.RelPath("example.tar.gz.sha512"): "metadata",
            },
        ),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "detect_archives_requiring_quarantine", return_value=[]),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(
            revision.interaction,
            "latest_revision",
            new_callable=mock.AsyncMock,
            side_effect=[old_revision, intervening_revision],
        ),
        mock.patch.object(revision.merge, "merge", new_callable=mock.AsyncMock),
        mock.patch.object(revision.sql, "Revision", side_effect=_make_fake_revision),
        mock.patch.object(revision, "SafeSession", return_value=MockSafeSession(safe_data)),
        mock.patch.object(revision, "_verify_provenance_sources_unchanged", new=verify_mock),
        mock.patch.object(revision.tasks, "draft_checks", new_callable=mock.AsyncMock),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "create_hard_link_clone", new_callable=mock.AsyncMock),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.paths, "get_archives_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={"example.tar.gz": 1}),
        mock.patch.object(revision.paths, "release_directory", return_value=temp_dir / "releases" / "00007"),
        mock.patch.object(revision.paths, "release_directory_base", return_value=temp_dir / "releases"),
    ]

    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            modify=modify,
        )

    assert verify_mock.await_count == 1
    assert verify_mock.await_args is not None
    assert verify_mock.await_args.kwargs["path_provenance"] == provenance_map
    assert verify_mock.await_args.kwargs["pre_merge_inodes"] == {"example.tar.gz": 1}


@pytest.mark.asyncio
async def test_modify_failed_error_propagates_and_cleans_up(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    received_args: dict[str, object] = {}

    async def modify(path: safe.StatePath, old_rev: object) -> None:
        received_args["path"] = path
        received_args["old_rev"] = old_rev
        (path / "file.txt").path.write_text("Should be cleaned up.")
        raise datatypes.FailedError("Intentional error")

    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.key = "proj"
    release.project_key = "proj"
    release.version = "1.0"
    release.latest_revision_number = "00001"
    mock_session = _mock_db_session(release)
    participant = _make_participant()

    with (
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.interaction, "latest_revision", new_callable=mock.AsyncMock, return_value=None),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
    ):
        with pytest.raises(datatypes.FailedError, match="Intentional error"):
            await participant.create_revision_with_quarantine(
                safe.ProjectKey("proj"),
                safe.VersionKey("1.0"),
                "test",
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                modify=modify,
            )

    assert isinstance(received_args["path"], safe.StatePath)
    assert received_args["old_rev"] is None
    assert not os.listdir(tmp_path)


@pytest.mark.asyncio
async def test_modify_path_provenance_flows_into_write_files_data(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.release_policy = None
    release.release_policy = None
    release.activity_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)

    import contextlib

    import atr.models.attestable as attestable_models

    provenance = attestable_models.ProvenanceV2(
        generator=attestable_models.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={"initiated_by": "alice", "source_paths": ["example.tar.gz"]},
    )
    provenance_map: dict[safe.RelPath, attestable_models.ProvenanceV2] = {
        safe.RelPath("example.tar.gz.sha512"): provenance,
    }

    async def modify(
        path: safe.StatePath, old_rev: object
    ) -> dict[safe.RelPath, attestable_models.ProvenanceV2] | None:
        return provenance_map

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    safe_data = MockSafeData(parent_key=None, new_number="00001")
    write_files_data_mock = mock.AsyncMock()
    compute_file_state_rows_mock = mock.MagicMock(return_value=[])

    patches = [
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(revision.attestable, "load", new_callable=mock.AsyncMock, return_value=None),
        mock.patch.object(
            revision.attestable,
            "paths_to_hashes_and_sizes",
            new_callable=mock.AsyncMock,
            return_value=(
                {
                    safe.RelPath("example.tar.gz"): "blake3:src",
                    safe.RelPath("example.tar.gz.sha512"): "blake3:hash",
                },
                {
                    safe.RelPath("example.tar.gz"): 100,
                    safe.RelPath("example.tar.gz.sha512"): 64,
                },
            ),
        ),
        mock.patch.object(revision.attestable, "write_files_data", new=write_files_data_mock),
        mock.patch.object(revision.attestable, "compute_file_state_rows", new=compute_file_state_rows_mock),
        mock.patch.object(
            revision.attestable,
            "compute_classifications",
            new_callable=mock.AsyncMock,
            return_value={
                safe.RelPath("example.tar.gz"): "source",
                safe.RelPath("example.tar.gz.sha512"): "metadata",
            },
        ),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "detect_archives_requiring_quarantine", return_value=[]),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(revision.interaction, "latest_revision", new_callable=mock.AsyncMock, return_value=None),
        mock.patch.object(revision.merge, "merge", new_callable=mock.AsyncMock),
        mock.patch.object(revision.sql, "Revision", side_effect=_make_fake_revision),
        mock.patch.object(revision, "SafeSession", return_value=MockSafeSession(safe_data)),
        mock.patch.object(revision.tasks, "draft_checks", new_callable=mock.AsyncMock),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "create_hard_link_clone", new_callable=mock.AsyncMock),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.paths, "get_archives_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(revision.paths, "release_directory", return_value=temp_dir / "releases" / "00001"),
        mock.patch.object(revision.paths, "release_directory_base", return_value=temp_dir / "releases"),
    ]

    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            modify=modify,
        )

    assert write_files_data_mock.await_count == 1
    forwarded = write_files_data_mock.await_args.kwargs.get("effective_path_provenance")
    assert forwarded == {"example.tar.gz.sha512": provenance}
    assert compute_file_state_rows_mock.call_count == 1
    forwarded_rows = compute_file_state_rows_mock.call_args.kwargs.get("effective_path_provenance")
    assert forwarded_rows == {"example.tar.gz.sha512": provenance}


@pytest.mark.asyncio
async def test_open_for_replace_rejects_directory_collision(tmp_path: pathlib.Path):
    # If the target's a directory (not a file), we leave it alone and let
    # the write fail. Don't want to silently drop a directory's contents on
    # a name clash.
    target = tmp_path / "collision"
    target.mkdir()
    (target / "inside.txt").write_bytes(b"inner")

    participant = _make_release_participant()
    open_for_replace = getattr(participant, "_CommitteeParticipant__open_for_replace")

    with pytest.raises(IsADirectoryError):
        async with open_for_replace(target) as handle:
            await handle.write(b"new bytes")

    assert target.is_dir()
    assert (target / "inside.txt").read_bytes() == b"inner"


@pytest.mark.asyncio
async def test_open_for_replace_silently_replaces_readonly_hardlink(tmp_path: pathlib.Path):
    # Regression for #1183. Re-upload should succeed when the target is a
    # hardlinked 0o444 file from a prior revision. And the prior revision's
    # bytes should stay intact - we break the shared inode rather than writing
    # through it.
    prior_revision = tmp_path / "prior"
    prior_revision.mkdir()
    prior_file = prior_revision / "artifact.tar.gz"
    prior_file.write_bytes(b"original bytes")

    new_revision = tmp_path / "new"
    new_revision.mkdir()
    target = new_revision / "artifact.tar.gz"
    os.link(prior_file, target)
    os.chmod(target, 0o444)
    assert target.stat().st_ino == prior_file.stat().st_ino

    participant = _make_release_participant()
    open_for_replace = getattr(participant, "_CommitteeParticipant__open_for_replace")

    async with open_for_replace(target) as handle:
        await handle.write(b"new bytes")

    assert target.read_bytes() == b"new bytes"
    assert prior_file.read_bytes() == b"original bytes"
    assert target.stat().st_ino != prior_file.stat().st_ino


@pytest.mark.asyncio
async def test_v1_previous_attestable_suppresses_file_state_rows(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.release_policy = None
    release.release_policy = None
    release.activity_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    release_key = sql.release_key("proj", "1.0")
    release.key = release_key

    old_revision = mock.MagicMock()
    old_revision.name = f"{release_key} 00001"
    old_revision.number = "00001"
    old_revision.safe_number = safe.RevisionNumber("00001")

    import contextlib

    import atr.models.attestable as attestable

    v1_attestable = attestable.AttestableV1(paths={"example.txt": "hash1"})

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    safe_data = MockSafeData(parent_key=old_revision.name, new_number="00002")

    patches = [
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(revision.attestable, "load", new_callable=mock.AsyncMock, return_value=v1_attestable),
        mock.patch.object(
            revision.attestable,
            "paths_to_hashes_and_sizes",
            new_callable=mock.AsyncMock,
            return_value=({"example.txt": "hash2"}, {"example.txt": 100}),
        ),
        mock.patch.object(revision.attestable, "write_files_data", new_callable=mock.AsyncMock),
        mock.patch.object(
            revision.attestable,
            "compute_classifications",
            new_callable=mock.AsyncMock,
            return_value={"example.txt": "binary"},
        ),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "detect_archives_requiring_quarantine", return_value=[]),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(
            revision.interaction, "latest_revision", new_callable=mock.AsyncMock, return_value=old_revision
        ),
        mock.patch.object(revision.merge, "merge", new_callable=mock.AsyncMock),
        mock.patch.object(revision.sql, "Revision", side_effect=_make_fake_revision),
        mock.patch.object(revision, "SafeSession", return_value=MockSafeSession(safe_data)),
        mock.patch.object(revision.tasks, "draft_checks", new_callable=mock.AsyncMock),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "create_hard_link_clone", new_callable=mock.AsyncMock),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(revision.paths, "release_directory", return_value=temp_dir / "releases" / "00002"),
        mock.patch.object(revision.paths, "release_directory_base", return_value=temp_dir / "releases"),
    ]

    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        await participant.create_revision_with_quarantine(
            "proj",
            "1.0",
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
        )

    added_objects = [call.args[0] for call in safe_data.add.call_args_list]
    file_state_rows = [obj for obj in added_objects if isinstance(obj, sql.ReleaseFileState)]
    assert file_state_rows == []


@pytest.mark.asyncio
async def test_verify_provenance_sources_unchanged_accepts_stable_inode(tmp_path: pathlib.Path):
    import atr.models.attestable as attestable

    source = tmp_path / "artifact.tar.gz"
    source.write_bytes(b"payload")
    inode = source.stat().st_ino

    provenance = attestable.ProvenanceV2(
        generator=attestable.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "initiated_by": "alice",
            "source_content_hashes": {"artifact.tar.gz": "blake3:x"},
            "source_paths": ["artifact.tar.gz"],
        },
    )
    await revision._verify_provenance_sources_unchanged(
        temp_dir_path=safe.StatePath(tmp_path),
        pre_merge_inodes={"artifact.tar.gz": inode},
        path_provenance={safe.RelPath("artifact.tar.gz.sha512"): provenance},
    )


@pytest.mark.asyncio
async def test_verify_provenance_sources_unchanged_accepts_stable_signature(tmp_path: pathlib.Path):
    import atr.models.attestable as attestable

    source = tmp_path / "artifact.tar.gz"
    source.write_bytes(b"payload")
    signature = tmp_path / "artifact.tar.gz.asc"
    signature.write_bytes(b"sig")

    provenance = attestable.ProvenanceV2(
        generator=attestable.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "initiated_by": "alice",
            "signature_path": "artifact.tar.gz.asc",
            "source_content_hashes": {"artifact.tar.gz": "blake3:x"},
            "source_paths": ["artifact.tar.gz"],
        },
    )
    await revision._verify_provenance_sources_unchanged(
        temp_dir_path=safe.StatePath(tmp_path),
        pre_merge_inodes={
            "artifact.tar.gz": source.stat().st_ino,
            "artifact.tar.gz.asc": signature.stat().st_ino,
        },
        path_provenance={safe.RelPath("artifact.tar.gz.sha512"): provenance},
    )


@pytest.mark.asyncio
async def test_verify_provenance_sources_unchanged_ignores_unrelated_file(tmp_path: pathlib.Path):
    import atr.models.attestable as attestable

    source = tmp_path / "artifact.tar.gz"
    source.write_bytes(b"payload")
    signature = tmp_path / "artifact.tar.gz.asc"
    signature.write_bytes(b"sig")
    source_inode = source.stat().st_ino
    signature_inode = signature.stat().st_ino

    provenance = attestable.ProvenanceV2(
        generator=attestable.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "initiated_by": "alice",
            "signature_path": "artifact.tar.gz.asc",
            "source_content_hashes": {"artifact.tar.gz": "blake3:x"},
            "source_paths": ["artifact.tar.gz"],
        },
    )
    await revision._verify_provenance_sources_unchanged(
        temp_dir_path=safe.StatePath(tmp_path),
        pre_merge_inodes={
            "artifact.tar.gz": source_inode,
            "artifact.tar.gz.asc": signature_inode,
            "unrelated.txt": 424242,
        },
        path_provenance={safe.RelPath("artifact.tar.gz.sha512"): provenance},
    )


@pytest.mark.asyncio
async def test_verify_provenance_sources_unchanged_rejects_missing_signature(tmp_path: pathlib.Path):
    import atr.models.attestable as attestable

    source = tmp_path / "artifact.tar.gz"
    source.write_bytes(b"payload")

    provenance = attestable.ProvenanceV2(
        generator=attestable.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "initiated_by": "alice",
            "signature_path": "artifact.tar.gz.asc",
            "source_content_hashes": {"artifact.tar.gz": "blake3:x"},
            "source_paths": ["artifact.tar.gz"],
        },
    )
    with pytest.raises(datatypes.FailedError, match=r"concurrent revision replaced artifact\.tar\.gz\.asc"):
        await revision._verify_provenance_sources_unchanged(
            temp_dir_path=safe.StatePath(tmp_path),
            pre_merge_inodes={
                "artifact.tar.gz": source.stat().st_ino,
                "artifact.tar.gz.asc": 123,
            },
            path_provenance={safe.RelPath("artifact.tar.gz.sha512"): provenance},
        )


@pytest.mark.asyncio
async def test_verify_provenance_sources_unchanged_rejects_missing_source(tmp_path: pathlib.Path):
    import atr.models.attestable as attestable

    provenance = attestable.ProvenanceV2(
        generator=attestable.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "initiated_by": "alice",
            "source_content_hashes": {"artifact.tar.gz": "blake3:x"},
            "source_paths": ["artifact.tar.gz"],
        },
    )
    with pytest.raises(datatypes.FailedError, match=r"concurrent revision replaced artifact\.tar\.gz"):
        await revision._verify_provenance_sources_unchanged(
            temp_dir_path=safe.StatePath(tmp_path),
            pre_merge_inodes={"artifact.tar.gz": 123},
            path_provenance={safe.RelPath("artifact.tar.gz.sha512"): provenance},
        )


@pytest.mark.asyncio
async def test_verify_provenance_sources_unchanged_rejects_replaced_signature(tmp_path: pathlib.Path):
    import atr.models.attestable as attestable

    source = tmp_path / "artifact.tar.gz"
    source.write_bytes(b"payload")
    signature = tmp_path / "artifact.tar.gz.asc"
    signature.write_bytes(b"sig")

    provenance = attestable.ProvenanceV2(
        generator=attestable.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "initiated_by": "alice",
            "signature_path": "artifact.tar.gz.asc",
            "source_content_hashes": {"artifact.tar.gz": "blake3:x"},
            "source_paths": ["artifact.tar.gz"],
        },
    )
    with pytest.raises(datatypes.FailedError, match=r"concurrent revision replaced artifact\.tar\.gz\.asc"):
        await revision._verify_provenance_sources_unchanged(
            temp_dir_path=safe.StatePath(tmp_path),
            pre_merge_inodes={
                "artifact.tar.gz": source.stat().st_ino,
                "artifact.tar.gz.asc": signature.stat().st_ino + 999,
            },
            path_provenance={safe.RelPath("artifact.tar.gz.sha512"): provenance},
        )


@pytest.mark.asyncio
async def test_verify_provenance_sources_unchanged_rejects_replaced_source(tmp_path: pathlib.Path):
    import atr.models.attestable as attestable

    source = tmp_path / "artifact.tar.gz"
    source.write_bytes(b"payload")
    stale_inode = source.stat().st_ino + 999

    provenance = attestable.ProvenanceV2(
        generator=attestable.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "initiated_by": "alice",
            "source_content_hashes": {"artifact.tar.gz": "blake3:x"},
            "source_paths": ["artifact.tar.gz"],
        },
    )
    with pytest.raises(datatypes.FailedError, match=r"concurrent revision replaced artifact\.tar\.gz"):
        await revision._verify_provenance_sources_unchanged(
            temp_dir_path=safe.StatePath(tmp_path),
            pre_merge_inodes={"artifact.tar.gz": stale_inode},
            path_provenance={safe.RelPath("artifact.tar.gz.sha512"): provenance},
        )


def _make_fake_revision(**kwargs) -> FakeRevision:
    return FakeRevision(**kwargs)


def _make_participant() -> revision.CommitteeParticipant:
    mock_write = mock.MagicMock()
    mock_write.authorisation.asf_uid = "test"
    return revision.CommitteeParticipant(mock_write, mock.MagicMock(), mock.MagicMock(), "test")


def _make_release_participant() -> release.CommitteeParticipant:
    mock_write = mock.MagicMock()
    mock_write.authorisation.asf_uid = "test"
    return release.CommitteeParticipant(mock_write, mock.MagicMock(), mock.MagicMock(), "test")


def _mock_db_session(release: mock.MagicMock, selected_revision: mock.MagicMock | None = None) -> mock.MagicMock:
    release.project.committee_key = "test"
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
