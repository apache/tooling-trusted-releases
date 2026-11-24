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

import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.db.interaction as interaction
import atr.errors as errors
import atr.models.safe as safe
import atr.models.sql as sql

_PROJECT_KEY = safe.ProjectKey("one")


@pytest.mark.parametrize(
    ("opted_in", "message"),
    [
        pytest.param(False, "has not allowed uploads from GitHub Actions", id="not-opted-in"),
        pytest.param(True, "no automated release signing key", id="no-key"),
    ],
)
async def test_a_committee_that_cannot_upload_is_refused(monkeypatch: pytest.MonkeyPatch, opted_in: bool, message: str):
    _patch(monkeypatch, _project(opted_in=opted_in), enabled=False)

    with pytest.raises(errors.ASFQuartException, match=message) as exc_info:
        await interaction.compose_project_for_payload(_payload(), "alice", _PROJECT_KEY)

    assert exc_info.value.errorcode == 403
    assert "https://atr.example/committees/one" in str(exc_info.value)


async def test_a_declared_project_is_returned(monkeypatch: pytest.MonkeyPatch):
    project = _project()
    _patch(monkeypatch, project)

    found = await interaction.compose_project_for_payload(_payload(), "alice", _PROJECT_KEY)

    assert found == ("alice", project)


async def test_a_listed_repository_matches_whatever_its_case(monkeypatch: pytest.MonkeyPatch):
    project = _project(repositories=["https://github.com/Apache/FIRST"])
    _patch(monkeypatch, project)

    found = await interaction.compose_project_for_payload(_payload(), "alice", _PROJECT_KEY)

    assert found == ("alice", project)


async def test_a_missing_project_is_not_found(monkeypatch: pytest.MonkeyPatch):
    _patch(monkeypatch, None)

    with pytest.raises(errors.ASFQuartException, match="Project 'one' not found") as exc_info:
        await interaction.compose_project_for_payload(_payload(), "alice", _PROJECT_KEY)

    assert exc_info.value.errorcode == 404


async def test_a_rerun_is_refused_with_or_without_a_project(monkeypatch: pytest.MonkeyPatch):
    _patch(monkeypatch, _project())
    lookup = mock.AsyncMock()
    monkeypatch.setattr(interaction, "_trusted_project", lookup)

    for project_key in (_PROJECT_KEY, None):
        with pytest.raises(errors.ASFQuartException, match="Start a new run") as exc_info:
            await interaction.compose_project_for_payload(_payload(run_attempt="2"), "alice", project_key)
        assert exc_info.value.errorcode == 403
    lookup.assert_not_awaited()


async def test_a_token_with_no_asf_user_is_refused(monkeypatch: pytest.MonkeyPatch):
    _patch(monkeypatch, _project())

    with pytest.raises(errors.ASFQuartException) as exc_info:
        await interaction.compose_project_for_payload(_payload(), None, _PROJECT_KEY)

    assert exc_info.value.errorcode == 403


async def test_an_archived_project_is_refused(monkeypatch: pytest.MonkeyPatch):
    _patch(monkeypatch, _project(status=sql.ProjectStatus.RETIRED))

    with pytest.raises(errors.ASFQuartException, match="archived") as exc_info:
        await interaction.compose_project_for_payload(_payload(), "alice", _PROJECT_KEY)

    assert exc_info.value.errorcode == 403


async def test_a_repository_outside_apache_is_refused_even_when_listed(monkeypatch: pytest.MonkeyPatch):
    _patch(monkeypatch, _project(repositories=["https://github.com/someone/first"]))
    payload = _payload()
    payload.repository = "someone/first"

    with pytest.raises(errors.ASFQuartException, match="not in the repository list") as exc_info:
        await interaction.compose_project_for_payload(payload, "alice", _PROJECT_KEY)

    assert exc_info.value.errorcode == 403


async def test_an_unlisted_repository_is_refused_and_says_where_to_fix_it(monkeypatch: pytest.MonkeyPatch):
    _patch(monkeypatch, _project(repositories=["https://github.com/apache/second"]))

    with pytest.raises(errors.ASFQuartException) as exc_info:
        await interaction.compose_project_for_payload(_payload(), "alice", _PROJECT_KEY)

    assert exc_info.value.errorcode == 403
    message = str(exc_info.value)
    assert "apache/first is not in the repository list of project 'one'" in message
    assert "https://atr.example/projects/one" in message


async def test_another_project_listing_the_repository_changes_nothing(monkeypatch: pytest.MonkeyPatch):
    project = _project()
    data = _patch(monkeypatch, project)

    await interaction.compose_project_for_payload(_payload(), "alice", _PROJECT_KEY)

    # Only the declared project is ever loaded, so no other project's list is read
    data.project.assert_called_once_with(key="one", _committee=True)
    data.release_policy.assert_not_called()


@pytest.mark.parametrize(
    ("committee_key", "opted_in", "key_held", "enabled"),
    [
        pytest.param("one", True, True, True, id="opted-in-with-key"),
        pytest.param("one", True, False, False, id="opted-in-key-lost"),
        pytest.param("one", False, True, False, id="key-but-not-opted-in"),
        pytest.param("tooling", False, False, True, id="tooling"),
        pytest.param("test", False, False, True, id="test"),
        pytest.param(None, True, True, False, id="no-committee"),
    ],
)
async def test_ci_builds_need_the_opt_in_and_a_signing_key(
    monkeypatch: pytest.MonkeyPatch, committee_key: str | None, opted_in: bool, key_held: bool, enabled: bool
):
    committee = sql.Committee(key=committee_key or "one", github_oidc_enabled=opted_in)
    data = mock.MagicMock()
    data.committee.return_value.get = mock.AsyncMock(return_value=committee)
    monkeypatch.setattr(interaction, "automated_release_signing_key_held", mock.AsyncMock(return_value=key_held))

    assert await interaction.ci_builds_enabled(committee_key, data) is enabled


async def test_without_a_project_the_workflow_path_lookup_decides(monkeypatch: pytest.MonkeyPatch):
    project = _project()
    _patch(monkeypatch, project)
    lookup = mock.AsyncMock(return_value=project)
    monkeypatch.setattr(interaction, "_trusted_project", lookup)

    found = await interaction.compose_project_for_payload(_payload(), "alice", None)

    assert found == ("alice", project)
    lookup.assert_awaited_once_with(
        "apache/first",
        "apache/first/.github/workflows/release.yml@refs/heads/main",
        interaction.TrustedProjectPhase.COMPOSE,
    )


def _patch(monkeypatch: pytest.MonkeyPatch, project: sql.Project | None, enabled: bool = True) -> mock.MagicMock:
    data = mock.MagicMock()
    data.project.return_value.get = mock.AsyncMock(return_value=project)
    data.project_security_options.return_value.get = mock.AsyncMock(return_value=None)
    session = mock.AsyncMock()
    session.__aenter__.return_value = data
    monkeypatch.setattr(interaction.db, "session", mock.MagicMock(return_value=session))
    monkeypatch.setattr(interaction, "ci_builds_enabled", mock.AsyncMock(return_value=enabled))
    monkeypatch.setattr(interaction.util, "absolute_url", lambda path: f"https://atr.example{path}")
    return data


def _payload(run_attempt: str = "1") -> SimpleNamespace:
    return SimpleNamespace(
        repository="apache/first",
        workflow_ref="apache/first/.github/workflows/release.yml@refs/heads/main",
        ref="refs/heads/main",
        run_attempt=run_attempt,
        run_id="99",
    )


def _project(
    repositories: list[str] | None = None,
    status: sql.ProjectStatus = sql.ProjectStatus.ACTIVE,
    opted_in: bool = True,
) -> sql.Project:
    return sql.Project(
        key="one",
        name="One",
        status=status,
        repositories=repositories if (repositories is not None) else ["https://github.com/apache/first.git"],
        committee=sql.Committee(key="one", name="One", github_oidc_enabled=opted_in),
    )
