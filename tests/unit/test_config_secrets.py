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

import logging
import pathlib

import pytest

import atr.config as config


def test_environment_override_warns(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    secrets = tmp_path / "secrets.ini"
    secrets.write_text("[settings]\nEXAMPLE_SECRET = from-file\n")
    monkeypatch.setenv("EXAMPLE_SECRET", "from-environment")
    with caplog.at_level(logging.WARNING):
        config._config_secrets_get(str(secrets), "EXAMPLE_SECRET")
    assert "EXAMPLE_SECRET" in caplog.text


def test_environment_overrides_secrets_file(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    secrets = tmp_path / "secrets.ini"
    secrets.write_text("[settings]\nEXAMPLE_SECRET = from-file\n")
    monkeypatch.setenv("EXAMPLE_SECRET", "from-environment")
    assert config._config_secrets_get(str(secrets), "EXAMPLE_SECRET") == "from-environment"


def test_secrets_file_used_without_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    secrets = tmp_path / "secrets.ini"
    secrets.write_text("[settings]\nEXAMPLE_SECRET = from-file\n")
    monkeypatch.delenv("EXAMPLE_SECRET", raising=False)
    assert config._config_secrets_get(str(secrets), "EXAMPLE_SECRET") == "from-file"
