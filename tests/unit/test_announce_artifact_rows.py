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
import hashlib
import pathlib
import types
import unittest.mock as mock

import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.announce as announce_writer


class SignatureResults:
    def __init__(self, current_evidence: bool) -> None:
        self.current_evidence = current_evidence

    def __call__(self, _project_key, _version_key, revision_number, **_kwargs):
        if str(revision_number) == "00002":
            return [types.SimpleNamespace(status=sql.CheckResultStatus.NOTE, data={"fingerprint": "a" * 40})]
        if self.current_evidence:
            return [types.SimpleNamespace(status=sql.CheckResultStatus.NOTE, data={"fingerprint": "b" * 40})]
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "has_parent", "current_evidence", "expected_fingerprint"),
    [
        (sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, False, True, "b" * 40),
        (sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, True, True, "b" * 40),
        (sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, True, False, None),
        (sql.ReleasePhase.RELEASE_CANDIDATE, True, False, "a" * 40),
        (sql.ReleasePhase.RELEASE_PREVIEW, True, False, "a" * 40),
    ],
)
async def test_write_artifact_rows_records_signature_digest(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, phase, has_parent, current_evidence, expected_fingerprint
) -> None:
    (tmp_path / "artifact.tar.gz").write_bytes(b"artifact bytes")
    signature_bytes = b"signature bytes"
    (tmp_path / "artifact.tar.gz.asc").write_bytes(signature_bytes)

    rows: list[sql.Artifact] = []
    data = mock.MagicMock()
    data.add = mock.MagicMock(side_effect=rows.append)
    data.release_file_classifications_at = mock.AsyncMock(return_value={})
    data.artifact.return_value.limit.return_value.get = mock.AsyncMock(return_value=None)
    parent = sql.Revision(number="00002", phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, asfuid="old-signer")
    revision = sql.Revision(number="00003", phase=phase, asfuid="new-signer", parent=parent if has_parent else None)
    data.revision.return_value.get = mock.AsyncMock(return_value=revision)
    data.execute = mock.AsyncMock(return_value=types.SimpleNamespace(scalar_one_or_none=lambda: True))
    check_results = mock.AsyncMock(side_effect=SignatureResults(current_evidence))
    monkeypatch.setattr(announce_writer.interaction, "check_results_for_revision", check_results)

    release = types.SimpleNamespace(
        project_key="foo",
        version="1.0",
        key="foo-1.0",
        safe_project_key=safe.ProjectKey("foo"),
        safe_version_key=safe.VersionKey("1.0"),
    )
    committee = types.SimpleNamespace(is_podling=False, key="tooling")
    writer = object.__new__(announce_writer.ReleaseManager)
    writer._ReleaseManager__data = data
    write_rows = getattr(writer, "_ReleaseManager__write_artifact_rows")

    await write_rows(
        release,
        committee,
        safe.StatePath(tmp_path),
        safe.RevisionNumber("00003"),
        42,
        None,
        datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
    )

    assert [row.artifact_path for row in rows] == ["artifact.tar.gz"]
    assert rows[0].signature_path == "artifact.tar.gz.asc"
    assert rows[0].signature_sha3_256 == hashlib.sha3_256(signature_bytes).hexdigest()
    assert rows[0].svn_revision == 42
    assert rows[0].key_fingerprint == expected_fingerprint
    assert check_results.await_args.kwargs["include_legacy_revision_results"] is True


@pytest.mark.asyncio
async def test_announce_refuses_a_version_catalogued_from_outside_atr() -> None:
    # The query leaves out the rows ATR wrote at publish, so a row found was catalogued elsewhere
    data = mock.MagicMock()
    data.artifact.return_value.limit.return_value.get = mock.AsyncMock(return_value=sql.Artifact())
    writer = object.__new__(announce_writer.ReleaseManager)
    writer._ReleaseManager__data = data
    ensure_uncatalogued = getattr(writer, "_ReleaseManager__ensure_uncatalogued")
    release = types.SimpleNamespace(project_key="foo", version="1.0")

    with pytest.raises(storage.AccessError, match=r"already in the release catalog$") as info:
        await ensure_uncatalogued(release)

    assert info.value.status == 409
    data.artifact.assert_called_once_with(project_key="foo", version="1.0")
    data.add.assert_not_called()


def _rows(*directories: str | None) -> list[sql.Artifact]:
    return [sql.Artifact(download_path_suffix=directory) for directory in directories]


def test_the_recorded_suffix_is_relative_to_the_committee_directory() -> None:
    committee = types.SimpleNamespace(is_podling=False, key="storm")
    recorded = announce_writer.release.recorded_download_path_suffix
    assert recorded(committee, _rows("storm", "storm")) is None
    assert str(recorded(committee, _rows("storm/apache-storm-3.2.0"))) == "apache-storm-3.2.0"


@pytest.mark.parametrize("directories", [("storm", "storm/apache-storm-3.2.0"), ("flink/1.0",), (None,)])
def test_rows_that_disagree_or_leave_the_committee_directory_have_no_suffix(directories) -> None:
    committee = types.SimpleNamespace(is_podling=False, key="storm")
    with pytest.raises(ValueError, match="recorded"):
        announce_writer.release.recorded_download_path_suffix(committee, _rows(*directories))
