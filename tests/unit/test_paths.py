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
import types

import pytest

import atr.paths as paths


def test_get_quarantined_dir_uses_state_dir(monkeypatch, tmp_path: pathlib.Path):
    mock_config = types.SimpleNamespace(STATE_DIR=str(tmp_path))
    monkeypatch.setattr("atr.config.get", lambda: mock_config)
    assert paths.get_quarantined_dir() == tmp_path / "quarantined"


def test_quarantine_directory_builds_deterministic_path(monkeypatch, tmp_path: pathlib.Path):
    mock_config = types.SimpleNamespace(STATE_DIR=str(tmp_path))
    monkeypatch.setattr("atr.config.get", lambda: mock_config)
    mock_release = types.SimpleNamespace(project_key="example", version="1.2.3")
    quarantined = types.SimpleNamespace(release=mock_release, token="0123456789abcdef")
    assert (
        paths.quarantine_directory(quarantined) == tmp_path / "quarantined" / "example" / "1.2.3" / "0123456789abcdef"
    )


def test_quarantine_directory_rejects_non_alnum_token():
    quarantined = types.SimpleNamespace(token="../escape")
    with pytest.raises(ValueError, match="Invalid quarantine token"):
        paths.quarantine_directory(quarantined)
