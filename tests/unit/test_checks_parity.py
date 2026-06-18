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
import pathlib

import pytest

import atr.attestable as attestable
import atr.models.attestable as models
import atr.models.safe as safe
import atr.models.sql as sql
import atr.tasks.checks as checks
import atr.tasks.checks.parity as parity
import atr.util as util
import tests.unit.recorders as recorders


def test_archive_format_stem_strips_known_suffixes() -> None:
    assert util.archive_format_stem("apache-foo-1.0-src.tar.gz") == "apache-foo-1.0-src"
    assert util.archive_format_stem("apache-foo-1.0-src.tgz") == "apache-foo-1.0-src"
    assert util.archive_format_stem("apache-foo-1.0-src.tar.bz2") == "apache-foo-1.0-src"
    assert util.archive_format_stem("apache-foo-1.0-src.tar.xz") == "apache-foo-1.0-src"
    assert util.archive_format_stem("apache-foo-1.0-src.zip") == "apache-foo-1.0-src"
    assert util.archive_format_stem("apache-foo-1.0-bin.jar") is None


async def test_cross_format_cache_key_distinguishes_sibling_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    release = _release()
    primary = "dist/apache-foo-1.0-src.tar.gz"

    monkeypatch.setattr(attestable, "load", _loader(_attestable_sibling("apache-foo-1.0-src.zip")))
    with_zip = await checks._resolve_cross_format_sibling_swhids(release, primary)

    monkeypatch.setattr(attestable, "load", _loader(_attestable_sibling("apache-foo-1.0-src.tgz")))
    with_tgz = await checks._resolve_cross_format_sibling_swhids(release, primary)

    assert with_zip != with_tgz


async def test_cross_format_concerns_on_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    recorder = await _run(monkeypatch, tmp_path, _attestable("swh:1:dir:abc", "swh:1:dir:def"))

    assert len(recorder.messages) == 1
    status, message, _data = recorder.messages[0]
    assert status == sql.CheckResultStatus.CONCERN.value
    assert message == "Cross format archive contents differ from a sibling archive"


async def test_cross_format_matches_tar_xz_sibling(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    recorder = await _run(monkeypatch, tmp_path, _attestable_sibling("apache-foo-1.0-src.tar.xz"))

    assert recorder.messages == [
        (
            sql.CheckResultStatus.NOTE.value,
            "Cross format archive contents match sibling archives",
            {
                "rel_path": "dist/apache-foo-1.0-src.tar.gz",
                "swhid": "swh:1:dir:abc",
                "matched": ["dist/apache-foo-1.0-src.tar.xz"],
            },
        )
    ]


async def test_cross_format_notes_matching_siblings(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    recorder = await _run(monkeypatch, tmp_path, _attestable("swh:1:dir:abc", "swh:1:dir:abc"))

    assert recorder.messages == [
        (
            sql.CheckResultStatus.NOTE.value,
            "Cross format archive contents match sibling archives",
            {
                "rel_path": "dist/apache-foo-1.0-src.tar.gz",
                "swhid": "swh:1:dir:abc",
                "matched": ["dist/apache-foo-1.0-src.zip"],
            },
        )
    ]


def _attestable(tar_swhid: str, zip_swhid: str) -> models.AttestableV2:
    return models.AttestableV2(
        hashes={
            "hash-tar": models.HashEntryV2(size=10, uploaders=[], swhid_dir_inner=tar_swhid),
            "hash-zip": models.HashEntryV2(size=11, uploaders=[], swhid_dir_inner=zip_swhid),
        },
        paths={
            "dist/apache-foo-1.0-src.tar.gz": models.PathEntryV2(content_hash="hash-tar", classification="source"),
            "dist/apache-foo-1.0-src.zip": models.PathEntryV2(content_hash="hash-zip", classification="source"),
        },
    )


def _attestable_sibling(sibling_basename: str) -> models.AttestableV2:
    swhid_dir = "swh:1:dir:abc"
    return models.AttestableV2(
        hashes={
            "hash-tar": models.HashEntryV2(size=10, uploaders=[], swhid_dir_inner=swhid_dir),
            "hash-sib": models.HashEntryV2(size=11, uploaders=[], swhid_dir_inner=swhid_dir),
        },
        paths={
            "dist/apache-foo-1.0-src.tar.gz": models.PathEntryV2(content_hash="hash-tar", classification="source"),
            f"dist/{sibling_basename}": models.PathEntryV2(content_hash="hash-sib", classification="source"),
        },
    )


def _loader(att: models.AttestableV2):
    async def _load(project_key: safe.ProjectKey, version_key: safe.VersionKey, revision: safe.RevisionNumber):
        return att

    return _load


def _release() -> sql.Release:
    release = sql.Release(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        version="1.0",
        project_key="test",
        created=datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC),
    )
    release._latest_revision_number = "00001"
    return release


async def _run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, att: models.AttestableV2
) -> recorders.RecorderStub:
    async def _load(project_key: safe.ProjectKey, version_key: safe.VersionKey, revision: safe.RevisionNumber):
        return att

    monkeypatch.setattr(attestable, "load", _load)
    recorder = recorders.RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.parity.across_formats")
    args = checks.FunctionArguments(
        recorder=recorders.get_recorder(recorder),
        asf_uid="tester",
        project_key=safe.ProjectKey("test"),
        version_key=safe.VersionKey("1.0"),
        revision_number=safe.RevisionNumber("00001"),
        primary_rel_path=safe.RelPath("dist/apache-foo-1.0-src.tar.gz"),
        extra_args={},
    )
    await parity.across_formats(args)
    return recorder
