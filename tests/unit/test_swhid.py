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

import atr.swhid as swhid


def test_directory_id_keeps_empty_directories(tmp_path: pathlib.Path) -> None:
    (tmp_path / "README").write_bytes(b"hello\n")
    script = tmp_path / "run.sh"
    script.write_bytes(b"#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    (tmp_path / "empty").mkdir()
    (tmp_path / "nested" / "alsoempty").mkdir(parents=True)
    (tmp_path / "link").symlink_to("README")

    assert str(swhid.directory_id_sync(tmp_path)) == "swh:1:dir:84247a5962788b2eabf97e07255f3c8b43cc7811"
