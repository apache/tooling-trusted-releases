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

import atr.models.args as args
import atr.models.safe as safe
import atr.tasks.svn as svn


def _fake_export(svn_command: list[str]) -> None:
    destination = pathlib.Path(svn_command[-1])
    destination.mkdir()
    (destination / "apache-example-1.0-src.tar.gz").write_bytes(b"artifact bytes")


async def test_export_into_moves_files_and_removes_temp_dir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_export = mock.AsyncMock(side_effect=_fake_export)
    monkeypatch.setattr(svn, "_import_files_core_run_svn_export", run_export)
    task_args = args.SvnImport(
        svn_url=safe.RelPath("dev/incubator/example/1.0-rc.1"),
        revision="HEAD",
        target_subdirectory=None,
        project_key=safe.ProjectKey("example"),
        version_key=safe.VersionKey("1.0"),
        asf_uid="tester",
    )

    await svn._export_into(safe.StatePath(tmp_path), None, task_args=task_args)

    assert os.listdir(tmp_path) == ["apache-example-1.0-src.tar.gz"]
    svn_command = run_export.await_args_list[0].args[0]
    assert svn_command[:5] == ["svn", "export", "--non-interactive", "--ignore-externals", "--ignore-keywords"]
    assert svn_command[-2] == "https://dist.apache.org/repos/dist/dev/incubator/example/1.0-rc.1@HEAD"
