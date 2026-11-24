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

import pytest

import atr.db.interaction as interaction
import atr.models.sql as sql

WORKFLOW_REF = "apache/first/.github/workflows/release.yml@refs/heads/main"


@pytest.mark.parametrize(
    ("policy_name", "metadata", "matches"),
    [
        pytest.param("first", [], True, id="named-repository"),
        pytest.param("elsewhere", ["https://github.com/apache/first"], False, id="named-other-repository"),
        pytest.param("", ["https://github.com/apache/first", "https://github.com/apache/second"], True, id="metadata"),
        pytest.param("", ["https://github.com/apache/second"], False, id="not-in-metadata"),
        pytest.param("", ["https://gitbox.apache.org/repos/asf/first.git"], False, id="not-github"),
    ],
)
async def test_matches_follow_the_policy_or_the_metadata(policy_name: str, metadata: list[str], matches: bool):
    project = sql.Project(key="one", name="One", repositories=metadata)
    data = _data({1: project})

    found = await interaction._trusted_project_matches(
        data, [sql.ReleasePolicy(id=1, github_repository_name=policy_name)], "apache/first"
    )

    assert found == ([project] if matches else [])


@pytest.mark.parametrize(
    ("phase", "accepted"),
    [
        (interaction.TrustedProjectPhase.COMPOSE, False),
        (interaction.TrustedProjectPhase.VOTE, True),
        (interaction.TrustedProjectPhase.FINISH, True),
    ],
)
async def test_only_uploads_wait_on_the_committee_allowing_them(
    monkeypatch: pytest.MonkeyPatch, phase: interaction.TrustedProjectPhase, accepted: bool
):
    # The committee holds a signing key and has not allowed uploads from GitHub Actions
    committee = sql.Committee(key="one", name="One", github_oidc_enabled=False)
    project = sql.Project(key="one", name="One", repositories=["https://github.com/apache/first"], committee=committee)
    data = _patch_lookup(monkeypatch, [sql.ReleasePolicy(id=1)], {1: project})
    data.committee.return_value.get = mock.AsyncMock(return_value=committee)
    key = interaction.AutomatedReleaseSigningKey(committee, "0" * 40, None)
    monkeypatch.setattr(interaction, "automated_release_signing_keys", mock.AsyncMock(return_value=[key]))

    if accepted:
        assert await interaction._trusted_project("apache/first", WORKFLOW_REF, phase) is project
        return
    with pytest.raises(interaction.InteractionError, match="not in a committee that can make automated releases"):
        await interaction._trusted_project("apache/first", WORKFLOW_REF, phase)


async def test_two_projects_with_the_same_repository_are_refused(monkeypatch: pytest.MonkeyPatch):
    first = sql.Project(key="one", name="One", repositories=["https://github.com/apache/first"])
    fork = sql.Project(key="fork", name="Fork", repositories=["https://github.com/apache/first"])
    _patch_lookup(monkeypatch, [sql.ReleasePolicy(id=1), sql.ReleasePolicy(id=2)], {1: first, 2: fork})

    with pytest.raises(interaction.InteractionError, match="more than one project: fork, one"):
        await interaction._trusted_project("apache/first", WORKFLOW_REF, interaction.TrustedProjectPhase.COMPOSE)


async def test_a_policy_without_a_project_is_skipped(monkeypatch: pytest.MonkeyPatch):
    project = sql.Project(
        key="one",
        name="One",
        repositories=["https://github.com/apache/first"],
        committee=sql.Committee(key="one", name="One"),
    )
    _patch_lookup(monkeypatch, [sql.ReleasePolicy(id=1), sql.ReleasePolicy(id=2)], {1: None, 2: project})
    signing = mock.AsyncMock(return_value=frozenset({"one"}))
    monkeypatch.setattr(interaction, "automated_release_signing_committees", signing)

    found = await interaction._trusted_project("apache/first", WORKFLOW_REF, interaction.TrustedProjectPhase.VOTE)

    assert found is project


async def test_an_upload_matches_the_whole_workflow_path(monkeypatch: pytest.MonkeyPatch):
    project = sql.Project(key="one", name="One", repositories=["https://github.com/apache/first"])
    data = _patch_lookup(monkeypatch, [], {1: project})
    # The query finds this row, since the stored text contains the path the workflow ran from
    longer = sql.ProjectSecurityOptions(
        project_key="one", github_upload_workflow_paths=[".github/workflows/release.yml.disabled"]
    )
    data.project_security_options.return_value.all = mock.AsyncMock(return_value=[longer])

    with pytest.raises(interaction.ReleasePolicyNotFoundError):
        await interaction._trusted_project("apache/first", WORKFLOW_REF, interaction.TrustedProjectPhase.COMPOSE)


async def test_no_matching_project_is_not_found(monkeypatch: pytest.MonkeyPatch):
    other = sql.Project(key="other", name="Other", repositories=["https://github.com/apache/second"])
    _patch_lookup(monkeypatch, [sql.ReleasePolicy(id=1)], {1: other})

    with pytest.raises(interaction.ReleasePolicyNotFoundError):
        await interaction._trusted_project("apache/first", WORKFLOW_REF, interaction.TrustedProjectPhase.COMPOSE)


async def test_one_matching_project_is_returned(monkeypatch: pytest.MonkeyPatch):
    project = sql.Project(
        key="one",
        name="One",
        repositories=["https://github.com/apache/first"],
        committee=sql.Committee(key="one", name="One"),
    )
    _patch_lookup(monkeypatch, [sql.ReleasePolicy(id=1)], {1: project})
    monkeypatch.setattr(interaction, "ci_builds_enabled", mock.AsyncMock(return_value=True))

    found = await interaction._trusted_project("apache/first", WORKFLOW_REF, interaction.TrustedProjectPhase.COMPOSE)

    assert found is project


def _data(projects: dict[int, sql.Project | None]) -> mock.MagicMock:
    by_key = {project.key: project for project in projects.values() if project is not None}

    # The vote and finish lookups go from a policy to its project, and the upload lookup goes by project key
    def project(release_policy_id: int | None = None, key: str | None = None, **_loads: bool) -> mock.MagicMock:
        query = mock.MagicMock()
        found = by_key.get(key) if (key is not None) else projects[release_policy_id or 0]
        query.get = mock.AsyncMock(return_value=found)
        return query

    data = mock.MagicMock()
    data.project.side_effect = project
    # Every project restricts its uploads to the workflow in WORKFLOW_REF
    options = [
        sql.ProjectSecurityOptions(project_key=key, github_upload_workflow_paths=[".github/workflows/release.yml"])
        for key in by_key
    ]
    data.project_security_options.return_value.all = mock.AsyncMock(return_value=options)
    return data


def _patch_lookup(
    monkeypatch: pytest.MonkeyPatch, policies: list[sql.ReleasePolicy], projects: dict[int, sql.Project | None]
) -> mock.MagicMock:
    data = _data(projects)
    data.release_policy.return_value.all = mock.AsyncMock(return_value=policies)
    session = mock.AsyncMock()
    session.__aenter__.return_value = data
    monkeypatch.setattr(interaction.db, "session", mock.MagicMock(return_value=session))
    return data
