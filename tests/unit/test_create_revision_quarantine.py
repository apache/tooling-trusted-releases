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

import contextlib
import pathlib
import unittest.mock as mock
from typing import Final

import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.datatypes as datatypes
import atr.storage.writers.revision as revision

_QUARANTINE_TOKEN_ALPHABET: Final[str] = "qpzry9x8gf2tvdw0s3jn54khce6mua7b"


class AsyncContextManager:
    async def __aenter__(self):
        return None

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


class FakeQuarantined:
    def __init__(self, **kwargs):
        self.id = 42
        self.release_key = kwargs.get("release_key", "")
        self.asf_uid = kwargs.get("asf_uid", "")
        self.prior_revision_key = kwargs.get("prior_revision_key")
        self.status = kwargs.get("status", sql.QuarantineStatus.STAGING)
        self.token = kwargs.get("token", "")
        self.created = kwargs.get("created")
        self.file_metadata = kwargs.get("file_metadata")
        self.description = kwargs.get("description")
        self.release = mock.MagicMock()


class MockQuarantineData:
    def __init__(self, latest_revision_key: str | None):
        self._added_objects: list[object] = []
        self._quarantined: FakeQuarantined | None = None
        self._latest_revision_key = latest_revision_key
        self._flush_count = 0
        self.add = mock.MagicMock(side_effect=self._add)
        self.begin = mock.MagicMock(return_value=AsyncContextManager())
        self.begin_immediate = mock.AsyncMock()
        self.commit = mock.AsyncMock(side_effect=self._commit)
        self.commit_snapshots: list[tuple[sql.QuarantineStatus | None, int]] = []
        self.flush = mock.AsyncMock(side_effect=self._flush)
        self.merge = mock.AsyncMock(side_effect=self._merge)
        self.refresh = mock.AsyncMock()

    def _add(self, obj: object) -> None:
        self._added_objects.append(obj)
        if isinstance(obj, FakeQuarantined):
            self._quarantined = obj

    async def _commit(self) -> None:
        task_count = sum(1 for obj in self._added_objects if not isinstance(obj, FakeQuarantined))
        status = self._quarantined.status if (self._quarantined is not None) else None
        self.commit_snapshots.append((status, task_count))

    async def _flush(self) -> None:
        self._flush_count += 1
        if (self._quarantined is not None) and (self._quarantined.id is None):
            self._quarantined.id = 42

    async def _merge(self, obj: object) -> object:
        return obj


class MockQuarantineSession:
    def __init__(self, data: MockQuarantineData):
        self._data = data

    async def __aenter__(self) -> MockQuarantineData:
        return self._data

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


def test_generate_quarantine_token_length_and_alphabet():
    token = revision._generate_quarantine_token()

    assert len(token) == 24
    assert all(c in _QUARANTINE_TOKEN_ALPHABET for c in token)


def test_generate_quarantine_token_uniqueness():
    tokens = {revision._generate_quarantine_token() for _ in range(100)}

    assert len(tokens) == 100


@pytest.mark.asyncio
async def test_no_quarantine_returns_revision_when_no_archives(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.release_policy = None
    release.project.key = "proj"
    release.project_key = "proj"
    release.version = "1.0"
    release.release_policy = None
    release.key = sql.release_key(release.project.key, release.version)
    release.latest_revision_number = "00001"

    mock_session = _mock_db_session(release)
    participant = _make_participant()

    fake_revision = mock.MagicMock(spec=sql.Revision)

    patches = [
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(
            revision.attestable,
            "paths_to_hashes_and_sizes",
            new_callable=mock.AsyncMock,
            return_value=({safe.RelPath("README.md"): "hash1"}, {safe.RelPath("README.md"): 100}),
        ),
        mock.patch.object(revision.attestable, "write_files_data", new_callable=mock.AsyncMock),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(revision.detection, "detect_archives_requiring_quarantine", return_value=[]),
        mock.patch.object(revision.interaction, "latest_revision", new_callable=mock.AsyncMock, return_value=None),
        mock.patch.object(revision, "_commit_new_revision", new_callable=mock.AsyncMock, return_value=fake_revision),
        mock.patch.object(
            revision, "_lock_and_merge", new_callable=mock.AsyncMock, return_value=(None, None, None, release)
        ),
        mock.patch.object(revision, "SafeSession", return_value=MockQuarantineSession(MockQuarantineData(None))),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(revision.attestable, "load", new_callable=mock.AsyncMock, return_value=None),
    ]

    with contextlib.ExitStack() as stack:
        _apply_patches(stack, patches)
        result = await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
        )

    assert result is fake_revision


@pytest.mark.asyncio
async def test_phase_gate_allows_matching_phase(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.release_policy = None
    release.project.key = "proj"
    release.project_key = "proj"
    release.version = "1.0"
    release.release_policy = None
    release.key = sql.release_key("proj", "1.0")
    release.latest_revision_number = "00001"

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    fake_revision = mock.MagicMock(spec=sql.Revision)

    patches = [
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(
            revision.attestable,
            "paths_to_hashes_and_sizes",
            new_callable=mock.AsyncMock,
            return_value=({"README.md": "hash1"}, {"README.md": 100}),
        ),
        mock.patch.object(revision.attestable, "write_files_data", new_callable=mock.AsyncMock),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(revision.detection, "detect_archives_requiring_quarantine", return_value=[]),
        mock.patch.object(revision.interaction, "latest_revision", new_callable=mock.AsyncMock, return_value=None),
        mock.patch.object(revision, "_commit_new_revision", new_callable=mock.AsyncMock, return_value=fake_revision),
        mock.patch.object(
            revision, "_lock_and_merge", new_callable=mock.AsyncMock, return_value=(None, None, None, release)
        ),
        mock.patch.object(revision, "SafeSession", return_value=MockQuarantineSession(MockQuarantineData(None))),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(revision.attestable, "load", new_callable=mock.AsyncMock, return_value=None),
    ]

    with contextlib.ExitStack() as stack:
        _apply_patches(stack, patches)
        result = await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
        )

    assert result is fake_revision


@pytest.mark.asyncio
async def test_phase_gate_rejects_mismatched_phase():
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE
    release.project.status = sql.ProjectStatus.ACTIVE

    mock_session = _mock_db_session(release)
    participant = _make_participant()

    with mock.patch.object(revision.db, "session", return_value=mock_session):
        with pytest.raises(
            datatypes.PhaseMismatchError,
            match="release phase is release_candidate",
        ):
            await participant.create_revision_with_quarantine(
                "proj",
                "1.0",
                "test",
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            )


@pytest.mark.asyncio
async def test_quarantine_branch_returns_quarantined_when_archives_detected(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.key = "proj"
    release.project_key = "proj"
    release.version = "1.0"
    release.key = sql.release_key("proj", "1.0")
    release.latest_revision_number = "00001"

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    safe_data = MockQuarantineData(latest_revision_key=None)

    quarantine_dir = temp_dir / "quarantine" / "proj" / "1.0" / "testtoken"

    patches = [
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(
            revision.attestable,
            "paths_to_hashes_and_sizes",
            new_callable=mock.AsyncMock,
            return_value=(
                {safe.RelPath("dist/apache-test-1.0.tar.gz"): "hash1"},
                {safe.RelPath("dist/apache-test-1.0.tar.gz"): 1000},
            ),
        ),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(
            revision.detection,
            "detect_archives_requiring_quarantine",
            return_value=[safe.RelPath("dist/apache-test-1.0.tar.gz")],
        ),
        mock.patch.object(
            revision.detection,
            "deduplicate_quarantine_archives",
            return_value=[(safe.RelPath("dist/apache-test-1.0.tar.gz"), "hash1")],
        ),
        mock.patch.object(revision.interaction, "latest_revision", new_callable=mock.AsyncMock, return_value=None),
        mock.patch.object(revision.sql, "Quarantined", side_effect=FakeQuarantined),
        mock.patch.object(revision.sql, "Task", side_effect=lambda **kwargs: mock.MagicMock(**kwargs)),
        mock.patch.object(revision, "SafeSession", return_value=MockQuarantineSession(safe_data)),
        mock.patch.object(revision, "_generate_quarantine_token", return_value="aaaaaaaaaaaaaaaa"),
        mock.patch.object(revision.paths, "quarantine_directory", return_value=quarantine_dir),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(revision.attestable, "load", new_callable=mock.AsyncMock, return_value=None),
    ]

    with contextlib.ExitStack() as stack:
        _apply_patches(stack, patches)
        result = await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
        )

    assert isinstance(result, FakeQuarantined)
    assert result.status == sql.QuarantineStatus.PENDING
    assert result.prior_revision_key is None
    assert result.file_metadata is not None
    assert len(result.file_metadata) == 1
    assert result.file_metadata[0].rel_path == "dist/apache-test-1.0.tar.gz"
    assert result.file_metadata[0].content_hash == "hash1"
    assert result.file_metadata[0].size_bytes == 1000

    assert safe_data.commit_snapshots == [
        (sql.QuarantineStatus.STAGING, 0),
        (sql.QuarantineStatus.PENDING, 1),
    ]


@pytest.mark.asyncio
async def test_quarantine_dedup_applied_to_task_args(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.project.key = "proj"
    release.project_key = "proj"
    release.version = "1.0"
    release.key = sql.release_key("proj", "1.0")
    release.latest_revision_number = "00001"

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    safe_data = MockQuarantineData(latest_revision_key=None)

    quarantine_dir = tmp_path / "quarantine" / "proj" / "1.0" / "testtoken"

    patches = [
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(
            revision.attestable,
            "paths_to_hashes_and_sizes",
            new_callable=mock.AsyncMock,
            return_value=(
                {
                    safe.RelPath("a/test.tar.gz"): "h1",
                    safe.RelPath("b/test.tar.gz"): "h1",
                    safe.RelPath("c/other.zip"): "h2",
                },
                {
                    safe.RelPath("a/test.tar.gz"): 100,
                    safe.RelPath("b/test.tar.gz"): 100,
                    safe.RelPath("c/other.zip"): 200,
                },
            ),
        ),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(
            revision.detection,
            "detect_archives_requiring_quarantine",
            return_value=[safe.RelPath("a/test.tar.gz"), safe.RelPath("b/test.tar.gz"), safe.RelPath("c/other.zip")],
        ),
        mock.patch.object(
            revision.detection,
            "deduplicate_quarantine_archives",
            return_value=[(safe.RelPath("a/test.tar.gz"), "h1"), (safe.RelPath("c/other.zip"), "h2")],
        ),
        mock.patch.object(revision.interaction, "latest_revision", new_callable=mock.AsyncMock, return_value=None),
        mock.patch.object(revision.sql, "Quarantined", side_effect=FakeQuarantined),
        mock.patch.object(revision.sql, "Task", side_effect=lambda **kwargs: mock.MagicMock(**kwargs)),
        mock.patch.object(revision, "SafeSession", return_value=MockQuarantineSession(safe_data)),
        mock.patch.object(revision, "_generate_quarantine_token", return_value="cccccccccccccccc"),
        mock.patch.object(revision.paths, "quarantine_directory", return_value=quarantine_dir),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(revision.attestable, "load", new_callable=mock.AsyncMock, return_value=None),
    ]

    with contextlib.ExitStack() as stack:
        _apply_patches(stack, patches)
        result = await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
        )

    assert isinstance(result, FakeQuarantined)

    assert safe_data.commit_snapshots == [
        (sql.QuarantineStatus.STAGING, 0),
        (sql.QuarantineStatus.PENDING, 1),
    ]

    task_objects = [obj for obj in safe_data._added_objects if not isinstance(obj, FakeQuarantined)]
    assert len(task_objects) == 1
    task = task_objects[0]
    archives = task.task_args["archives"]
    assert len(archives) == 2
    assert archives[0] == {"rel_path": "a/test.tar.gz", "content_hash": "h1"}
    assert archives[1] == {"rel_path": "c/other.zip", "content_hash": "h2"}


@pytest.mark.asyncio
async def test_quarantine_stores_prior_revision_key_from_lock(tmp_path: pathlib.Path):
    temp_dir = safe.StatePath(tmp_path)
    release = mock.MagicMock()
    release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    release.project = mock.MagicMock()
    release.project.status = sql.ProjectStatus.ACTIVE
    release.key = sql.release_key("proj", "1.0")

    old_revision = mock.MagicMock()
    old_revision.key = f"{release.key} 00003"
    old_revision.number = "00003"

    mock_session = _mock_db_session(release)
    participant = _make_participant()
    safe_data = MockQuarantineData(latest_revision_key=old_revision.key)

    quarantine_dir = tmp_path / "quarantine" / "proj" / "1.0" / "testtoken"

    patches = [
        mock.patch.object(revision.aiofiles.os, "makedirs", new_callable=mock.AsyncMock),
        mock.patch.object(revision.aiofiles.os, "rename", new_callable=mock.AsyncMock),
        mock.patch.object(
            revision.attestable,
            "paths_to_hashes_and_sizes",
            new_callable=mock.AsyncMock,
            return_value=({safe.RelPath("dist/archive.tar.gz"): "newhash"}, {safe.RelPath("dist/archive.tar.gz"): 500}),
        ),
        mock.patch.object(revision.db, "session", return_value=mock_session),
        mock.patch.object(revision.detection, "validate_directory", return_value=[]),
        mock.patch.object(
            revision.detection,
            "detect_archives_requiring_quarantine",
            return_value=[safe.RelPath("dist/archive.tar.gz")],
        ),
        mock.patch.object(
            revision.detection,
            "deduplicate_quarantine_archives",
            return_value=[(safe.RelPath("dist/archive.tar.gz"), "newhash")],
        ),
        mock.patch.object(
            revision.interaction,
            "latest_revision",
            new_callable=mock.AsyncMock,
            side_effect=[old_revision, old_revision],
        ),
        mock.patch.object(revision.sql, "Quarantined", side_effect=FakeQuarantined),
        mock.patch.object(revision.sql, "Task", side_effect=lambda **kwargs: mock.MagicMock(**kwargs)),
        mock.patch.object(revision, "SafeSession", return_value=MockQuarantineSession(safe_data)),
        mock.patch.object(revision, "_generate_quarantine_token", return_value="bbbbbbbbbbbbbbbb"),
        mock.patch.object(revision.paths, "quarantine_directory", return_value=quarantine_dir),
        mock.patch.object(revision.paths, "get_tmp_dir", return_value=temp_dir),
        mock.patch.object(revision.util, "chmod_directories"),
        mock.patch.object(revision.util, "chmod_files"),
        mock.patch.object(revision.util, "create_hard_link_clone", new_callable=mock.AsyncMock),
        mock.patch.object(revision.util, "paths_to_inodes", return_value={}),
        mock.patch.object(
            revision.attestable, "load", new_callable=mock.AsyncMock, return_value=mock.MagicMock(paths={})
        ),
        mock.patch.object(revision.paths, "release_directory_base", return_value=temp_dir / "releases"),
        mock.patch.object(revision.paths, "release_directory", return_value=temp_dir / "releases" / "00003"),
    ]

    with contextlib.ExitStack() as stack:
        _apply_patches(stack, patches)
        result = await participant.create_revision_with_quarantine(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            "test",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
        )

    assert isinstance(result, FakeQuarantined)
    assert result.prior_revision_key == f"{release.key} 00003"
    assert result.status == sql.QuarantineStatus.PENDING

    assert safe_data.commit_snapshots == [
        (sql.QuarantineStatus.STAGING, 0),
        (sql.QuarantineStatus.PENDING, 1),
    ]


def _apply_patches(stack: contextlib.ExitStack, patches: list[mock._patch]) -> None:
    for p in patches:
        stack.enter_context(p)


def _make_participant() -> revision.CommitteeParticipant:
    mock_write = mock.MagicMock()
    mock_write.authorisation.asf_uid = "test"
    return revision.CommitteeParticipant(mock_write, mock.MagicMock(), mock.MagicMock(), "test")


def _mock_db_session(release: mock.MagicMock) -> mock.MagicMock:
    release.project.committee_key = "test"
    mock_query = mock.MagicMock()
    mock_query.demand = mock.AsyncMock(return_value=release)
    mock_data = mock.AsyncMock()
    mock_data.release = mock.MagicMock(return_value=mock_query)
    mock_data.__aenter__ = mock.AsyncMock(return_value=mock_data)
    mock_data.__aexit__ = mock.AsyncMock(return_value=False)
    return mock_data
