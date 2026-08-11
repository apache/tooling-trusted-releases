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

import atr.admin as admin
import atr.config as config
import atr.models.sql as sql


def release(phase: sql.ReleasePhase, project_key: str, version: str) -> mock.MagicMock:
    result = mock.MagicMock()
    result.phase = phase
    result.project_key = project_key
    result.version = version
    return result


async def test_consistency_database_dirs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "FINISHED_STORAGE_DIR", str(tmp_path / "finished"), raising=False)
    monkeypatch.setattr(config.get(), "UNFINISHED_STORAGE_DIR", str(tmp_path / "unfinished"), raising=False)
    (tmp_path / "finished" / "old" / "1.0").mkdir(parents=True)
    old_world = release(sql.ReleasePhase.RELEASE, "old", "1.0")
    new_world = release(sql.ReleasePhase.RELEASE, "newer", "2.0")
    draft = release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, "draft", "3.0")

    dirs = await admin._consistency_database_dirs([old_world, new_world, draft])

    assert dirs == [
        str(tmp_path / "finished" / "old" / "1.0"),
        str(tmp_path / "unfinished" / "draft" / "3.0"),
    ]
