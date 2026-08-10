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
import shutil
import types

import pytest

import atr.svn as svn

requires_svn = pytest.mark.skipif(
    (shutil.which("svn") is None) or (shutil.which("svnadmin") is None),
    reason="The Subversion command line tools are not available",
)


@requires_svn
async def test_publish_release_ignores_auto_props(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = tmp_path / "repository"
    await svn.run_command("svnadmin", "create", str(repository))
    home = tmp_path / "home"
    (home / ".subversion").mkdir(parents=True)
    (home / ".subversion" / "config").write_text(
        "[miscellany]\nenable-auto-props = yes\n[auto-props]\n*.txt = svn:eol-style=CRLF\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(svn.config, "get", lambda: types.SimpleNamespace(SVN_TOKEN="dummy"))
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"one\ntwo\n")
    url = f"file://{repository}/release"

    revision = await svn.publish_release(source, url, "tester", "Publish")

    assert revision == 1
    properties = await svn.run_command("svn", "proplist", "-v", f"{url}/a.txt@1")
    assert properties == ""
