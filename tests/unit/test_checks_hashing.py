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

import hashlib
import pathlib

import atr.models.safe as safe
import atr.models.sql as sql
import atr.tasks.checks as checks
import atr.tasks.checks.hashing as hashing
import tests.unit.recorders as recorders


async def test_check_accepts_freebsd_sha512_format(tmp_path: pathlib.Path) -> None:
    artifact_path, hash_path, expected_hash = _write_hash_fixture(tmp_path, "SHA512 ({name}) = {digest}\n")
    recorder = await _run_hash_check(hash_path)

    assert recorder.messages == [
        (
            sql.CheckResultStatus.SUCCESS.value,
            "Hash (sha512) matches expected value",
            {"computed_hash": expected_hash, "expected_hash": expected_hash},
        )
    ]
    assert artifact_path.exists()


async def test_check_retains_hash_filename_format(tmp_path: pathlib.Path) -> None:
    _artifact_path, hash_path, expected_hash = _write_hash_fixture(tmp_path, "{digest}  {name}\n")
    recorder = await _run_hash_check(hash_path)

    assert recorder.messages == [
        (
            sql.CheckResultStatus.SUCCESS.value,
            "Hash (sha512) matches expected value",
            {"computed_hash": expected_hash, "expected_hash": expected_hash},
        )
    ]


async def _run_hash_check(hash_path: pathlib.Path) -> recorders.RecorderStub:
    recorder = recorders.RecorderStub(safe.StatePath(hash_path), "atr.tasks.checks.hashing.check")
    args = checks.FunctionArguments(
        recorder=recorders.get_recorder(recorder),
        asf_uid="tester",
        project_key=safe.ProjectKey("test"),
        version_key=safe.VersionKey("1.0"),
        revision_number=safe.RevisionNumber("00001"),
        primary_rel_path=safe.RelPath(hash_path.name),
        extra_args={},
    )
    await hashing.check(args)
    return recorder


def _write_hash_fixture(tmp_path: pathlib.Path, hash_format: str) -> tuple[pathlib.Path, pathlib.Path, str]:
    artifact_path = tmp_path / "artifact.tar.gz"
    artifact_path.write_bytes(b"payload")
    expected_hash = hashlib.sha512(b"payload").hexdigest()
    hash_path = tmp_path / "artifact.tar.gz.sha512"
    hash_path.write_text(hash_format.format(name=artifact_path.name, digest=expected_hash))
    return artifact_path, hash_path, expected_hash
