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
import subprocess

import pytest

import atr.sandbox as sandbox


def landlock_works() -> bool:
    argv = sandbox.command(["true"])
    if argv == ["true"]:
        return False
    return subprocess.run(argv, capture_output=True, timeout=30).returncode == 0


requires_landlock = pytest.mark.skipif(
    not landlock_works(),
    reason="Landlock via setpriv is not available",
)


def test_command_passthrough_without_setpriv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "landlock_setpriv", lambda: None)
    assert sandbox.command(["rsync", "--server"], ro_paths=["/a"]) == ["rsync", "--server"]


def test_command_wraps_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "landlock_setpriv", lambda: "/bin/setpriv")
    wrapped = sandbox.command(["rsync", "--server"], ro_paths=["/a"], rw_paths=["/b"])
    assert wrapped[:3] == ["/bin/setpriv", "--landlock-access", "fs"]
    assert f"path-beneath:{sandbox.RO_ACCESSES}:/a" in wrapped
    assert f"path-beneath:{sandbox.RW_ACCESSES}:/b" in wrapped
    assert wrapped[-3:] == ["--", "rsync", "--server"]


@requires_landlock
def test_denies_read_outside_granted_paths(tmp_path: pathlib.Path) -> None:
    granted = tmp_path / "granted"
    granted.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    argv = sandbox.command(["cat", str(secret)], ro_paths=[str(granted)])
    result = subprocess.run(argv, capture_output=True)
    assert result.returncode != 0


@requires_landlock
def test_executes_command_with_granted_read(tmp_path: pathlib.Path) -> None:
    granted = tmp_path / "granted"
    granted.mkdir()
    inside = granted / "inside.txt"
    inside.write_text("inside")
    argv = sandbox.command(["cat", str(inside)], ro_paths=[str(granted)])
    result = subprocess.run(argv, capture_output=True)
    assert result.returncode == 0
    assert result.stdout == b"inside"


@requires_landlock
def test_write_operations_with_granted_rw(tmp_path: pathlib.Path) -> None:
    granted = tmp_path / "granted"
    granted.mkdir()
    outside = tmp_path / "outside.txt"
    script = 'echo data > "$1"/a.txt && mv "$1"/a.txt "$1"/b.txt && rm "$1"/b.txt'
    argv = sandbox.command(["sh", "-c", script, "sh", str(granted)], rw_paths=[str(granted)])
    assert subprocess.run(argv, capture_output=True).returncode == 0
    argv = sandbox.command(["sh", "-c", 'echo data > "$1"', "sh", str(outside)], rw_paths=[str(granted)])
    assert subprocess.run(argv, capture_output=True).returncode != 0
