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
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest

import atr.models.attestable as attestable
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.types as types
import atr.storage.writers.release as release


class CheckResultQuery:
    def __init__(self, results: list[object]) -> None:
        self._results = results

    def order_by(self, *args: object, **kwargs: object) -> "CheckResultQuery":
        return self

    async def all(self) -> list[object]:
        return self._results


class CheckResultSession:
    def __init__(self, results: list[object]) -> None:
        self.kwargs: dict[str, object] | None = None
        self._results = results

    def check_result(self, **kwargs: object) -> CheckResultQuery:
        self.kwargs = kwargs
        return CheckResultQuery(self._results)

    async def __aenter__(self) -> "CheckResultSession":
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> bool:
        return False


@pytest.mark.asyncio
async def test_generate_hash_file_creates_hash_file_and_returns_provenance(tmp_path: pathlib.Path):
    rel_path = pathlib.Path("artifact.tar.gz")
    (tmp_path / rel_path).write_bytes(b"payload")
    (tmp_path / "artifact.tar.gz.asc").write_bytes(b"signature")
    state_path = safe.StatePath(tmp_path)
    old_revision = SimpleNamespace(safe_number=safe.RevisionNumber("00001"))
    captured: dict[str, object] = {}

    async def create_revision_with_quarantine(
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        asf_uid: str,
        *,
        allowed_phases: frozenset[sql.ReleasePhase],
        description: str | None = None,
        modify=None,
    ) -> None:
        captured["allowed_phases"] = allowed_phases
        captured["description"] = description
        if modify is None:
            raise RuntimeError("Expected modify callback")
        captured["path_provenance"] = await modify(state_path, old_revision)

    participant = _make_participant(create_revision_with_quarantine)

    with (
        mock.patch.object(
            release,
            "_signature_provenance_metadata_for",
            new=mock.AsyncMock(
                return_value={
                    "fingerprint": "ABCD",
                    "key_id": "1234",
                    "signature_path": "artifact.tar.gz.asc",
                    "timestamp": "1713612345",
                    "username": "alice",
                }
            ),
        ),
        mock.patch.object(
            release.hashes,
            "compute_sha512_and_content_hash",
            new=mock.AsyncMock(return_value=("sha512hex", "blake3:source")),
        ),
    ):
        await participant.generate_hash_file(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            rel_path,
        )

    assert (tmp_path / "artifact.tar.gz.sha512").read_text() == "sha512hex  artifact.tar.gz\n"
    assert captured["allowed_phases"] == frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT})
    assert captured["description"] == "Hash generation through web interface"
    assert captured["path_provenance"] == {
        safe.RelPath("artifact.tar.gz.sha512"): attestable.ProvenanceV2(
            generator=attestable.GeneratorV2.SHA512_FROM_SIGNATURE,
            metadata={
                "fingerprint": "ABCD",
                "initiated_by": "alice",
                "key_id": "1234",
                "signature_path": "artifact.tar.gz.asc",
                "source_content_hashes": {"artifact.tar.gz": "blake3:source"},
                "source_paths": ["artifact.tar.gz"],
                "timestamp": "1713612345",
                "username": "alice",
            },
        )
    }


@pytest.mark.asyncio
async def test_generate_hash_file_requires_signature_file(tmp_path: pathlib.Path):
    rel_path = pathlib.Path("artifact.tar.gz")
    (tmp_path / rel_path).write_bytes(b"payload")
    state_path = safe.StatePath(tmp_path)
    old_revision = SimpleNamespace(safe_number=safe.RevisionNumber("00001"))

    async def create_revision_with_quarantine(
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        asf_uid: str,
        *,
        allowed_phases: frozenset[sql.ReleasePhase],
        description: str | None = None,
        modify=None,
    ) -> None:
        if modify is None:
            raise RuntimeError("Expected modify callback")
        await modify(state_path, old_revision)

    participant = _make_participant(create_revision_with_quarantine)

    with mock.patch.object(
        release,
        "_signature_provenance_metadata_for",
        new=mock.AsyncMock(),
    ) as signature_metadata_mock:
        with pytest.raises(types.FailedError, match=r"requires a detached OpenPGP signature"):
            await participant.generate_hash_file(
                safe.ProjectKey("proj"),
                safe.VersionKey("1.0"),
                rel_path,
            )

    assert signature_metadata_mock.await_count == 0


@pytest.mark.asyncio
async def test_signature_provenance_metadata_for_filters_unknown_fields():
    parent_revision = SimpleNamespace(safe_number=safe.RevisionNumber("00005"))
    result = SimpleNamespace(
        message="Signature verified successfully",
        status=sql.CheckResultStatus.SUCCESS,
        data={
            "fingerprint": "ABCDEF",
            "key_id": "Not available",
            "timestamp": "1713612345",
            "username": " unknown ",
        },
    )
    session = CheckResultSession([result])

    with mock.patch.object(release.db, "session", return_value=session):
        metadata = await release._signature_provenance_metadata_for(
            project_key=safe.ProjectKey("proj"),
            version_key=safe.VersionKey("1.0"),
            parent_revision=parent_revision,
            signature_rel_path=pathlib.Path("artifact.tar.gz.asc"),
        )

    assert metadata == {
        "fingerprint": "ABCDEF",
        "signature_path": "artifact.tar.gz.asc",
        "timestamp": "1713612345",
    }
    assert session.kwargs == {
        "checker": release._SIGNATURE_CHECKER_KEY,
        "primary_rel_path": "artifact.tar.gz.asc",
        "release_key": "proj-1.0",
        "revision_number": "00005",
    }


@pytest.mark.asyncio
async def test_signature_provenance_metadata_for_requires_completed_check():
    parent_revision = SimpleNamespace(safe_number=safe.RevisionNumber("00005"))
    session = CheckResultSession([])

    with (
        mock.patch.object(release.db, "session", return_value=session),
        mock.patch.object(release.log, "info") as log_info_mock,
    ):
        with pytest.raises(types.FailedError, match=r"has not completed yet"):
            await release._signature_provenance_metadata_for(
                project_key=safe.ProjectKey("proj"),
                version_key=safe.VersionKey("1.0"),
                parent_revision=parent_revision,
                signature_rel_path=pathlib.Path("artifact.tar.gz.asc"),
            )
    assert log_info_mock.call_count == 1
    assert "waiting for signature verification" in log_info_mock.call_args.args[0]


@pytest.mark.asyncio
async def test_signature_provenance_metadata_for_requires_parent_revision():
    with pytest.raises(types.FailedError, match=r"requires a parent revision"):
        await release._signature_provenance_metadata_for(
            project_key=safe.ProjectKey("proj"),
            version_key=safe.VersionKey("1.0"),
            parent_revision=None,
            signature_rel_path=pathlib.Path("artifact.tar.gz.asc"),
        )


@pytest.mark.asyncio
async def test_signature_provenance_metadata_for_requires_successful_check():
    parent_revision = SimpleNamespace(safe_number=safe.RevisionNumber("00005"))
    result = SimpleNamespace(
        message="No valid signature found",
        status=sql.CheckResultStatus.FAILURE,
        data={},
    )
    session = CheckResultSession([result])

    with (
        mock.patch.object(release.db, "session", return_value=session),
        mock.patch.object(release.log, "info") as log_info_mock,
    ):
        with pytest.raises(types.FailedError, match=r"failed: No valid signature found"):
            await release._signature_provenance_metadata_for(
                project_key=safe.ProjectKey("proj"),
                version_key=safe.VersionKey("1.0"),
                parent_revision=parent_revision,
                signature_rel_path=pathlib.Path("artifact.tar.gz.asc"),
            )
    assert log_info_mock.call_count == 1
    assert "status=failure" in log_info_mock.call_args.args[0]


def _make_participant(
    create_revision_with_quarantine: Callable[..., Awaitable[None]],
) -> release.CommitteeParticipant:
    mock_write = mock.MagicMock()
    mock_write.authorisation.asf_uid = "alice"
    mock_write_as = mock.MagicMock()
    mock_write_as.revision.create_revision_with_quarantine = mock.AsyncMock(side_effect=create_revision_with_quarantine)
    return release.CommitteeParticipant(mock_write, mock_write_as, mock.MagicMock(), "test")
