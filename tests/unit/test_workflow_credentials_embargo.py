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

import inspect
import unittest.mock as mock
from types import SimpleNamespace

import pytest
import werkzeug.exceptions as exceptions

import atr.api as api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.ssh as ssh
import atr.storage as storage
import atr.storage.writers.release as release_writer

_PROJECT_KEY = safe.ProjectKey("example")
_VERSION_KEY = safe.VersionKey("1.0.0")


def test_committee_rows_are_serialised_without_the_upload_setting() -> None:
    committee = sql.Committee(key="example", github_oidc_enabled=True)

    assert committee.github_oidc_enabled is True
    assert "github_oidc_enabled" not in committee.model_dump()
    assert "github_oidc_enabled" not in committee.model_dump_json()


@pytest.mark.asyncio
async def test_personal_key_of_a_member_still_reaches_an_embargoed_draft() -> None:
    project, release = _embargoed()

    await ssh._step_06b_validate_write_permissions("alice", project, release, False)
    found, patterns = await ssh._step_06a_validate_read_permissions(
        "alice", project, release, _PROJECT_KEY, _VERSION_KEY, None, False, False
    )

    assert (found, patterns) == (release, None)


@pytest.mark.asyncio
async def test_personal_token_store_into_a_missing_release_is_refused_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(exceptions.NotFound, match=r"no draft release example 1\.0\.0"):
        await _store(monkeypatch, None, from_workflow=False)


@pytest.mark.asyncio
async def test_project_token_store_gets_one_answer_for_missing_embargoed_and_voting_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project, embargoed = _embargoed()
    answers = set()
    for release in (None, embargoed, _release(sql.ReleasePhase.RELEASE_CANDIDATE)):
        with pytest.raises(exceptions.HTTPException) as refused:
            await _store(monkeypatch, release, from_workflow=True)
        answers.add((refused.value.code, refused.value.description))

    assert len(answers) == 1


@pytest.mark.asyncio
async def test_release_create_answers_for_an_embargoed_draft_as_for_any_draft() -> None:
    project, embargoed = _embargoed()
    answers = set()
    for release in (embargoed, _release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)):
        data = mock.MagicMock()
        data.release.return_value.get = mock.AsyncMock(return_value=release)
        with pytest.raises(storage.AccessError) as refused:
            await release_writer._assert_no_existing_release(data, project, _PROJECT_KEY, _VERSION_KEY)
        answers.add((refused.value.status, str(refused.value)))

    assert len(answers) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("member", "release_manager", "allowed"),
    [
        pytest.param(True, False, True, id="pmc-member"),
        pytest.param(False, True, True, id="release-manager"),
        pytest.param(False, False, False, id="committer"),
    ],
)
async def test_ssh_creates_a_release_for_members_and_release_managers(
    member: bool, release_manager: bool, allowed: bool
) -> None:
    committee = sql.Committee(
        key="example",
        committee_members=["alice"] if member else [],
        committers=["alice"],
        release_managers=["alice"] if release_manager else [],
    )
    project = sql.Project(key="example", name="Example", committee=committee)

    if allowed:
        await ssh._step_06b_validate_write_permissions("alice", project, None, True)
        return
    with pytest.raises(ssh.RsyncArgsError, match="PMC member or release manager"):
        await ssh._step_06b_validate_write_permissions("alice", project, None, True)


@pytest.mark.asyncio
async def test_workflow_key_read_gets_one_answer_for_missing_embargoed_and_finished_releases() -> None:
    project, embargoed = _embargoed()

    messages = set()
    for release in (None, embargoed, _release(sql.ReleasePhase.RELEASE, project)):
        with pytest.raises(ssh.RsyncArgsError) as refused:
            await ssh._step_06a_validate_read_permissions(
                "alice", project, release, _PROJECT_KEY, _VERSION_KEY, None, False, True
            )
        messages.add(str(refused.value))

    assert len(messages) == 1


@pytest.mark.asyncio
async def test_workflow_key_sees_an_embargoed_release_as_it_sees_a_finished_one() -> None:
    # Upload then download, the two things a workflow can try with its key
    project, embargoed = _embargoed()

    sequences = set()
    for release in (embargoed, _release(sql.ReleasePhase.RELEASE, project)):
        with pytest.raises(ssh.RsyncArgsError) as write_refused:
            await ssh._step_06b_validate_write_permissions("alice", project, release, True)
        with pytest.raises(ssh.RsyncArgsError) as read_refused:
            await ssh._step_06a_validate_read_permissions(
                "alice", project, release, _PROJECT_KEY, _VERSION_KEY, None, False, True
            )
        sequences.add((str(write_refused.value), str(read_refused.value)))

    assert len(sequences) == 1


@pytest.mark.asyncio
async def test_workflow_key_upload_refusal_is_the_same_for_embargoed_and_later_phases() -> None:
    project, embargoed = _embargoed()
    voting = _release(sql.ReleasePhase.RELEASE_CANDIDATE, project)

    messages = set()
    for release in (embargoed, voting):
        with pytest.raises(ssh.RsyncArgsError) as exc_info:
            await ssh._step_06b_validate_write_permissions("alice", project, release, True)
        messages.add(str(exc_info.value))

    assert len(messages) == 1


def _embargoed() -> tuple[sql.Project, sql.Release]:
    committee = sql.Committee(key="example", committee_members=["alice"], committers=["alice"])
    project = sql.Project(key="example", name="Example", committee=committee)
    release = sql.Release(
        key="example-1.0.0",
        project_key="example",
        version="1.0.0",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        expedited=True,
        project=project,
    )
    return project, release


def _release(phase: sql.ReleasePhase, project: sql.Project | None = None) -> sql.Release:
    return sql.Release(key="example-1.0.0", project_key="example", version="1.0.0", phase=phase, project=project)


async def _store(monkeypatch: pytest.MonkeyPatch, release: sql.Release | None, *, from_workflow: bool) -> None:
    github_payload = mock.MagicMock() if from_workflow else None
    data = mock.MagicMock()
    data.release.return_value.get = mock.AsyncMock(return_value=release)
    session = mock.AsyncMock()
    session.__aenter__.return_value = data
    monkeypatch.setattr(api.db, "session", mock.MagicMock(return_value=session))
    monkeypatch.setattr(api, "_jwt_asf_uid", lambda: "alice")
    monkeypatch.setattr(api, "_release_store_sources", mock.AsyncMock(return_value=(github_payload, None, True)))
    query = SimpleNamespace(
        project="example", version="1.0.0", relpath="a.tar.gz", expected_revision=None, source_commit=None
    )
    await inspect.unwrap(api.release_store)("release/store", query)
