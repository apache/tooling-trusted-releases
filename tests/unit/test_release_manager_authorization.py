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

import atr.models.safe as safe
import atr.storage as storage
import atr.storage.outcome as outcome


class ProjectQuery:
    def __init__(self, project_value: object | None) -> None:
        self.project_value = project_value

    async def demand(self, error: Exception) -> object:
        if self.project_value is None:
            raise error
        return self.project_value


def authorisation(
    asf_uid: str | None,
    *,
    member_of: frozenset[str] = frozenset(),
) -> SimpleNamespace:
    return SimpleNamespace(
        asf_uid=asf_uid,
        is_member_of=lambda key: key in member_of,
        is_participant_of=lambda key: key in member_of,
    )


def committee(
    key: str = "alpha",
    *,
    release_managers: list[str] | None = None,
    committers: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        release_managers=release_managers or [],
        committers=committers or [],
    )


def project(committee_obj: object) -> SimpleNamespace:
    return SimpleNamespace(key="example", committee=committee_obj)


@pytest.mark.asyncio
async def test_release_manager_outcome_raises_for_missing_project() -> None:
    auth = authorisation("alice")
    with pytest.raises(storage.AccessError, match="Project not found"):
        await write(auth, None).as_project_release_manager_outcome(safe.ProjectKey("missing"))


@pytest.mark.asyncio
async def test_release_manager_outcome_rejects_anonymous_caller() -> None:
    auth = authorisation(None)
    proj = project(committee(release_managers=["alice"], committers=["alice"]))
    result = await write(auth, proj).as_project_release_manager_outcome(safe.ProjectKey("example"))

    assert isinstance(result, outcome.Error)
    assert isinstance(result.error_or_none(), storage.AccessError)
    assert "Not authorized" in str(result.error_or_none())


@pytest.mark.asyncio
async def test_release_manager_outcome_rejects_orphan_project() -> None:
    auth = authorisation("alice")
    proj = project(None)
    result = await write(auth, proj).as_project_release_manager_outcome(safe.ProjectKey("example"))

    assert isinstance(result, outcome.Error)
    assert isinstance(result.error_or_none(), storage.AccessError)
    assert "No committee found" in str(result.error_or_none())


@pytest.mark.asyncio
async def test_release_manager_outcome_rejects_plain_committer() -> None:
    auth = authorisation("dave")
    proj = project(committee(release_managers=["alice"], committers=["alice", "dave"]))
    result = await write(auth, proj).as_project_release_manager_outcome(safe.ProjectKey("example"))

    assert isinstance(result, outcome.Error)
    assert isinstance(result.error_or_none(), storage.AccessError)
    assert "Not a release manager" in str(result.error_or_none())


@pytest.mark.asyncio
async def test_release_manager_outcome_rejects_release_manager_listing_without_committer_membership() -> None:
    auth = authorisation("alice")
    proj = project(committee(release_managers=["alice"], committers=["bob"]))
    result = await write(auth, proj).as_project_release_manager_outcome(safe.ProjectKey("example"))

    assert isinstance(result, outcome.Error)
    assert isinstance(result.error_or_none(), storage.AccessError)
    assert "Not a release manager" in str(result.error_or_none())


@pytest.mark.asyncio
async def test_release_manager_outcome_succeeds_for_designated_release_manager() -> None:
    auth = authorisation("alice")
    proj = project(committee(release_managers=["alice"], committers=["alice"]))
    result = await write(auth, proj).as_project_release_manager_outcome(safe.ProjectKey("example"))

    assert isinstance(result, outcome.Result)


@pytest.mark.asyncio
async def test_release_manager_outcome_succeeds_for_pmc_member() -> None:
    auth = authorisation("chair", member_of=frozenset({"alpha"}))
    proj = project(committee(release_managers=[], committers=[]))
    result = await write(auth, proj).as_project_release_manager_outcome(safe.ProjectKey("example"))

    assert isinstance(result, outcome.Result)


def write(auth: object, project_value: object | None) -> storage.Write:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=ProjectQuery(project_value))
    return storage.Write(auth, data)
