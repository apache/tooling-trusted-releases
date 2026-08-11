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

import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.validate as validate


def release(phase: sql.ReleasePhase) -> mock.MagicMock:
    result = mock.MagicMock()
    result.phase = phase
    result.key = "proj-1.0"
    result.project_key = "proj"
    result.version = "1.0"
    return result


def test_release_on_disk_flags_lingering_unfinished_directory(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "unfinished" / "proj" / "1.0").mkdir(parents=True)
    monkeypatch.setattr(validate.paths, "get_unfinished_dir", lambda: safe.StatePath(tmp_path / "unfinished"))

    divergences = list(validate.release_on_disk(release(sql.ReleasePhase.RELEASE)))

    assert len(divergences) == 1


def test_release_on_disk_flags_missing_draft_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        validate.paths, "release_directory", lambda _release: safe.StatePath(tmp_path / "unfinished" / "proj" / "1.0")
    )

    divergences = list(validate.release_on_disk(release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)))

    assert len(divergences) == 1


def test_release_on_disk_passes_released_without_directories(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate.paths, "get_unfinished_dir", lambda: safe.StatePath(tmp_path / "unfinished"))

    divergences = list(validate.release_on_disk(release(sql.ReleasePhase.RELEASE)))

    assert divergences == []
