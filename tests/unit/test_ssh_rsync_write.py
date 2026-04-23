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

"""Tests for SSH rsync write handling."""

from __future__ import annotations

from typing import TYPE_CHECKING

import atr.ssh as ssh

if TYPE_CHECKING:
    import pathlib


def test_build_rsync_write_argv_adds_server_side_limits(tmp_path: pathlib.Path) -> None:
    argv = ["rsync", "--server", "-vlogDtpre.iLsfxCIvu", "--delete", ".", "/proj/v1/"]
    path = tmp_path / "atr-write" / "revision"

    result = ssh._build_rsync_write_argv(argv, path)

    assert result == [
        "rsync",
        "--server",
        "-vlogDtpre.iLsfxCIvu",
        "--delete",
        f"--max-size={ssh._RSYNC_MAX_UPLOAD_SIZE}",
        "--info=skip2",
        ".",
        str(path),
    ]
    assert argv == ["rsync", "--server", "-vlogDtpre.iLsfxCIvu", "--delete", ".", "/proj/v1/"]
