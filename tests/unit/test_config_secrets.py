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


def test_config_secrets_rejects_relative_state_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAMPLE_SECRET", "from-environment")
    with pytest.raises(RuntimeError, match="absolute"):
        config._config_secrets("EXAMPLE_SECRET", "relative/state")


def test_environment_override_warns(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    secrets_path = tmp_path / "secrets.ini"
    secrets_path.write_text("[settings]\nEXAMPLE_SECRET = from-file\n")
    monkeypatch.setenv("EXAMPLE_SECRET", "from-environment")
    with caplog.at_level(logging.WARNING):
        config._config_secrets_get(str(secrets_path), "EXAMPLE_SECRET")
    assert "EXAMPLE_SECRET" in caplog.text


def test_environment_overrides_secrets_file(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    secrets_path = tmp_path / "secrets.ini"
    secrets_path.write_text("[settings]\nEXAMPLE_SECRET = from-file\n")
    monkeypatch.setenv("EXAMPLE_SECRET", "from-environment")
    assert config._config_secrets_get(str(secrets_path), "EXAMPLE_SECRET") == "from-environment"


def test_root_secrets_file_not_read(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    (tmp_path / "secrets.ini").write_text("[settings]\nEXAMPLE_SECRET = from-root\n")
    monkeypatch.delenv("EXAMPLE_SECRET", raising=False)
    assert config._config_secrets("EXAMPLE_SECRET", str(tmp_path)) is None
    curated = tmp_path / "secrets" / "curated"
    curated.mkdir(parents=True)
    (curated / "secrets.ini").write_text("[settings]\nEXAMPLE_SECRET = from-curated\n")
    assert config._config_secrets("EXAMPLE_SECRET", str(tmp_path)) == "from-curated"


def test_secrets_file_used_without_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    secrets_path = tmp_path / "secrets.ini"
    secrets_path.write_text("[settings]\nEXAMPLE_SECRET = from-file\n")
    monkeypatch.delenv("EXAMPLE_SECRET", raising=False)
    assert config._config_secrets_get(str(secrets_path), "EXAMPLE_SECRET") == "from-file"


def test_validate_rejects_root_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(config.AppConfig, "STATE_DIR", str(tmp_path))
    (tmp_path / "secrets.ini").write_text("[settings]\n")
    with pytest.raises(RuntimeError, match=r"secrets\.ini"):
        config.validate()


def test_validate_rejects_system_state_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    for state_dir in ["/usr/local/var/atr", "/usr", "/opt/../usr/local/var/atr"]:
        monkeypatch.setattr(config.AppConfig, "STATE_DIR", state_dir)
        with pytest.raises(RuntimeError, match="STATE_DIR"):
            config.validate()
