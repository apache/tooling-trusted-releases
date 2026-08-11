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

import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths


def _storage_config(tmp_path: pathlib.Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        UNFINISHED_STORAGE_DIR=str(tmp_path / "unfinished"),
        EMBARGOED_STORAGE_DIR=str(tmp_path / "embargoed"),
        FINISHED_STORAGE_DIR=str(tmp_path / "finished"),
    )


def _release(phase: sql.ReleasePhase, embargoed: bool, revision: str | None = "00001") -> types.SimpleNamespace:
    # is_embargoed is a property on the real model; here we set it directly so each branch is
    # exercised in isolation, independent of the expedited/phase combination that produces it.
    return types.SimpleNamespace(
        project_key="example",
        version="1.2.3",
        phase=phase,
        is_embargoed=embargoed,
        latest_revision_number=revision,
    )


def test_committee_dist_relpath_for_podling() -> None:
    committee = types.SimpleNamespace(key="myproject", is_podling=True)

    assert paths.committee_dist_relpath(
        committee,
        safe.RelPath("myproject-1.0"),
        "apache-myproject-1.0.tar.gz",
    ) == safe.RelPath("incubator/myproject/myproject-1.0/apache-myproject-1.0.tar.gz")


def test_committee_dist_relpath_for_top_level_committee() -> None:
    committee = types.SimpleNamespace(key="myproject", is_podling=False)

    assert paths.committee_dist_relpath(committee) == safe.RelPath("myproject")


def test_committee_keys_url_for_podling() -> None:
    committee = types.SimpleNamespace(key="myproject", is_podling=True)

    assert paths.committee_keys_url(committee) == "https://downloads.apache.org/incubator/myproject/KEYS"


def test_committee_keys_url_for_top_level_committee() -> None:
    committee = types.SimpleNamespace(key="myproject", is_podling=False)

    assert paths.committee_keys_url(committee) == "https://downloads.apache.org/myproject/KEYS"


def test_committee_keys_url_ignores_application_and_svn_hosts(monkeypatch) -> None:
    committee = types.SimpleNamespace(key="myproject", is_podling=False)
    monkeypatch.setattr(
        paths.config,
        "get",
        lambda: types.SimpleNamespace(
            APP_HOST="atr.example.invalid",
            SVN_PUBLISH_URL="https://svn.example.invalid/repos/dist/atr",
            SVN_DIST_PUBLIC_URL="https://dist.example.invalid/repos/dist/atr",
        ),
    )

    assert paths.committee_keys_url(committee) == "https://downloads.apache.org/myproject/KEYS"


def test_get_quarantined_dir_uses_state_dir(monkeypatch, tmp_path: pathlib.Path):
    mock_config = types.SimpleNamespace(STATE_DIR=str(tmp_path))
    monkeypatch.setattr("atr.config.get", lambda: mock_config)
    assert paths.get_quarantined_dir().path == tmp_path / "quarantined"


def test_quarantine_directory_builds_deterministic_path(monkeypatch, tmp_path: pathlib.Path):
    mock_config = types.SimpleNamespace(STATE_DIR=str(tmp_path))
    monkeypatch.setattr("atr.config.get", lambda: mock_config)
    mock_release = types.SimpleNamespace(project_key="example", version="1.2.3")
    quarantined = types.SimpleNamespace(release=mock_release, token="0123456789abcdef")
    assert (
        paths.quarantine_directory(quarantined).path
        == tmp_path / "quarantined" / "example" / "1.2.3" / "0123456789abcdef"
    )


def test_quarantine_directory_rejects_non_alnum_token():
    quarantined = types.SimpleNamespace(token="../escape")
    with pytest.raises(ValueError, match="Invalid quarantine token"):
        paths.quarantine_directory(quarantined)


def test_get_embargoed_dir_uses_embargoed_storage_dir(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    assert paths.get_embargoed_dir().path == tmp_path / "embargoed"


def test_release_directory_base_routes_embargoed_release_to_embargoed_root(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    release = _release(sql.ReleasePhase.RELEASE_CANDIDATE, embargoed=True)
    assert paths.release_directory_base(release).path == tmp_path / "embargoed" / "example" / "1.2.3"


def test_release_directory_base_routes_unembargoed_draft_to_unfinished_root(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    release = _release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, embargoed=False)
    assert paths.release_directory_base(release).path == tmp_path / "unfinished" / "example" / "1.2.3"


def test_release_directory_base_routes_released_to_finished_root(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    release = _release(sql.ReleasePhase.RELEASE, embargoed=False)
    assert paths.release_directory_base(release).path == tmp_path / "finished" / "example" / "1.2.3"


def test_release_directory_includes_revision_under_embargoed_root(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    release = _release(sql.ReleasePhase.RELEASE_PREVIEW, embargoed=True, revision="00007")
    assert paths.release_directory(release).path == tmp_path / "embargoed" / "example" / "1.2.3" / "00007"


def test_release_directory_revision_uses_embargoed_root(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    release = _release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, embargoed=True, revision="00003")
    revision_dir = paths.release_directory_revision(release)
    assert revision_dir is not None
    assert revision_dir.path == tmp_path / "embargoed" / "example" / "1.2.3" / "00003"


def test_release_directory_version_uses_embargoed_root(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    release = _release(sql.ReleasePhase.RELEASE_CANDIDATE, embargoed=True)
    assert paths.release_directory_version(release).path == tmp_path / "embargoed" / "example" / "1.2.3"


def test_base_path_for_revision_defaults_to_unfinished_root(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    result = paths.base_path_for_revision("example", "1.2.3", "00001")
    assert result.path == tmp_path / "unfinished" / "example" / "1.2.3" / "00001"


def test_base_path_for_revision_uses_embargoed_root_when_flagged(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    result = paths.base_path_for_revision("example", "1.2.3", "00001", embargoed=True)
    assert result.path == tmp_path / "embargoed" / "example" / "1.2.3" / "00001"


def test_get_unfinished_dir_for_uses_embargoed_root_when_flagged(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    result = paths.get_unfinished_dir_for("example", "1.2.3", "00001", embargoed=True)
    assert result.path == tmp_path / "embargoed" / "example" / "1.2.3" / "00001"


def test_revision_path_for_file_uses_embargoed_root_when_flagged(monkeypatch, tmp_path: pathlib.Path):
    monkeypatch.setattr("atr.config.get", lambda: _storage_config(tmp_path))
    result = paths.revision_path_for_file("example", "1.2.3", "00001", "file.tar.gz", embargoed=True)
    assert result.path == tmp_path / "embargoed" / "example" / "1.2.3" / "00001" / "file.tar.gz"
