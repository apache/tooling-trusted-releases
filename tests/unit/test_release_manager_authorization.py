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
    is_podling: bool = False,
    release_managers: list[str] | None = None,
    committers: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        is_podling=is_podling,
        release_managers=release_managers or [],
        committers=committers or [],
    )


class ReleaseQuery:
    def __init__(self, release_value: object | None) -> None:
        self.release_value = release_value

    async def get(self) -> object | None:
        return self.release_value


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


@pytest.mark.parametrize("security_override", [False, True])
def test_security_override_is_not_a_resolve_argument(security_override: bool) -> None:
    officer_write = write(authorisation("officer", member_of=frozenset({"alpha", "security"})), None)
    data = mock.MagicMock()
    resolvers = [
        storage.WriteAsReleaseManager(officer_write, data, "alpha").vote.resolve,
        storage.WriteAsSecurityTeam(officer_write, data, "alpha").vote_resolve,
    ]
    for resolve in resolvers:
        with pytest.raises(TypeError, match="security_override"):
            resolve(
                safe.ProjectKey("example"),
                safe.VersionKey("1.0.0"),
                "passed",
                "Officer",
                "The vote passed.",
                security_override=security_override,
            )


def test_security_team_construction_rejects_pmc_member_outside_security_team() -> None:
    pmc_write = write(authorisation("chair", member_of=frozenset({"alpha"})), None)
    with pytest.raises(storage.AccessError) as facade_error:
        storage.WriteAsSecurityTeam(pmc_write, mock.MagicMock(), "alpha")
    with pytest.raises(storage.AccessError) as writer_error:
        storage.writers.vote.SecurityTeam(pmc_write, mock.MagicMock(), mock.MagicMock(), "alpha")

    assert facade_error.value.status == 403
    assert writer_error.value.status == 403


@pytest.mark.asyncio
async def test_security_team_outcome_rejects_pmc_member_outside_security_team() -> None:
    auth = authorisation("chair", member_of=frozenset({"alpha"}))
    proj = project(committee())
    result = await write(auth, proj).as_project_security_team_outcome(safe.ProjectKey("example"))

    assert isinstance(result, outcome.Error)
    assert "Not a member of the ASF Security Team" in str(result.error_or_none())


@pytest.mark.asyncio
async def test_security_team_outcome_succeeds_for_security_team_owner() -> None:
    auth = authorisation("officer", member_of=frozenset({"security"}))
    proj = project(committee())
    result = await write(auth, proj).as_project_security_team_outcome(safe.ProjectKey("example"))

    assert isinstance(result, outcome.Result)


@pytest.mark.asyncio
async def test_vote_committee_member_outcome_admits_active_round_committee() -> None:
    round_one_rel = SimpleNamespace(podling_thread_id=None)
    round_two_rel = SimpleNamespace(podling_thread_id="thread-1")
    podling_comm = committee("podling", is_podling=True)
    proj = project(podling_comm)

    ppmc_auth = authorisation("ppmc", member_of=frozenset({"podling"}))
    ipmc_auth = authorisation("ipmc", member_of=frozenset({"incubator"}))

    r1_ppmc = await write(ppmc_auth, proj, round_one_rel).as_vote_committee_member_outcome(
        safe.ProjectKey("example"), safe.VersionKey("1.0.0")
    )
    r1_ipmc = await write(ipmc_auth, proj, round_one_rel).as_vote_committee_member_outcome(
        safe.ProjectKey("example"), safe.VersionKey("1.0.0")
    )
    r2_ppmc = await write(ppmc_auth, proj, round_two_rel).as_vote_committee_member_outcome(
        safe.ProjectKey("example"), safe.VersionKey("1.0.0")
    )
    r2_ipmc = await write(ipmc_auth, proj, round_two_rel).as_vote_committee_member_outcome(
        safe.ProjectKey("example"), safe.VersionKey("1.0.0")
    )

    assert isinstance(r1_ppmc, outcome.Result)
    assert isinstance(r1_ipmc, outcome.Error)
    assert isinstance(r2_ppmc, outcome.Error)
    assert isinstance(r2_ipmc, outcome.Result)


@pytest.mark.asyncio
async def test_vote_resolver_outcome_admits_ipmc_only_in_podling_round_two() -> None:
    podling_comm = committee("podling", is_podling=True, release_managers=["rm"], committers=["rm"])
    tlp_comm = committee("alpha", is_podling=False)
    round_one_rel = SimpleNamespace(podling_thread_id=None)
    round_two_rel = SimpleNamespace(podling_thread_id="thread-1")
    ipmc_auth = authorisation("ipmc", member_of=frozenset({"incubator"}))
    rm_auth = authorisation("rm")

    r1_ipmc = await write(ipmc_auth, project(podling_comm), round_one_rel).as_vote_resolver_outcome(
        safe.ProjectKey("example"), safe.VersionKey("1.0.0")
    )
    tlp_ipmc = await write(ipmc_auth, project(tlp_comm), round_two_rel).as_vote_resolver_outcome(
        safe.ProjectKey("example"), safe.VersionKey("1.0.0")
    )
    r2_ipmc = await write(ipmc_auth, project(podling_comm), round_two_rel).as_vote_resolver_outcome(
        safe.ProjectKey("example"), safe.VersionKey("1.0.0")
    )
    r2_rm = await write(rm_auth, project(podling_comm), round_two_rel).as_vote_resolver_outcome(
        safe.ProjectKey("example"), safe.VersionKey("1.0.0")
    )

    assert isinstance(r1_ipmc, outcome.Error)
    assert isinstance(tlp_ipmc, outcome.Error)
    assert isinstance(r2_ipmc, outcome.Result)
    assert isinstance(r2_rm, outcome.Result)

    resolver = r2_ipmc.result_or_raise()
    assert not hasattr(resolver, "policy")
    assert not hasattr(resolver, "ssh")
    with pytest.raises(storage.AccessError, match="Not authorized for this release version"):
        await resolver.resolve(safe.ProjectKey("other"), safe.VersionKey("1.0.0"), "passed", "IPMC")


def write(auth: object, project_value: object | None, release_value: object | None = None) -> storage.Write:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=ProjectQuery(project_value))
    data.release = mock.MagicMock(return_value=ReleaseQuery(release_value))
    return storage.Write(auth, data)
