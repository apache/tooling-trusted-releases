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
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.announce as announce
import atr.storage.writers.checks as checks
import atr.storage.writers.distributions as distributions
import atr.storage.writers.release as release
import atr.storage.writers.revision as revision
import atr.storage.writers.sbom as sbom
import atr.storage.writers.vote as vote


@pytest.mark.asyncio
async def test_announce_release_blocks_retired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        announce.util,
        "permitted_announce_recipients",
        lambda _uid, committee_key: {"dev@project.apache.org"},
    )
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(_retired_release()))
    writer = object.__new__(announce.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "tester"
    writer._CommitteeMember__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.release(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            "dev@project.apache.org",
            "body",
            None,
            "tester",
            "Chair",
        )


@pytest.mark.asyncio
async def test_checks_ignore_add_propagates_chokepoint_block() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=_query_returning(_retired_project()))
    writer = object.__new__(checks.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "tester"
    writer._CommitteeMember__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.ignore_add(safe.ProjectKey("project"))


@pytest.mark.asyncio
async def test_distributions_automate_blocks_retired() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=_query_returning(_retired_project()))
    writer = object.__new__(distributions.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "tester"
    writer._CommitteeMember__committee_key = "project"

    platform = next(iter(sql.DistributionPlatform))
    with pytest.raises(storage.AccessError, match="archived"):
        await writer.automate(
            safe.ReleaseKey("project-1.0.0"),
            platform,
            "project",
            None,
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            "release",
            None,
            safe.Alphanumeric("pkg"),
            safe.VersionKey("1.0.0"),
            False,
        )


@pytest.mark.asyncio
async def test_distributions_record_blocks_retired_via_release_lookup() -> None:
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(_retired_release()))
    writer = object.__new__(distributions.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "tester"
    writer._CommitteeMember__committee_key = "project"

    platform = next(iter(sql.DistributionPlatform))
    with pytest.raises(storage.AccessError, match="archived"):
        await writer.record(
            safe.ReleaseKey("project-1.0.0"),
            platform,
            None,
            safe.Alphanumeric("pkg"),
            safe.VersionKey("1.0.0"),
            False,
            False,
            None,
        )


@pytest.mark.asyncio
async def test_release_delete_blocks_retired() -> None:
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(_retired_release()))
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.delete(safe.ProjectKey("project"), safe.VersionKey("1.0.0"))


@pytest.mark.asyncio
async def test_release_import_from_svn_blocks_retired() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=_query_returning(_retired_project()))
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.import_from_svn(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RelPath("svn/url"),
            "r1",
            None,
        )


@pytest.mark.asyncio
async def test_release_promote_to_candidate_blocks_retired() -> None:
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(_retired_release()))
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    # promote_to_candidate catches AccessError internally and returns it as a string
    result = await writer.promote_to_candidate(
        safe.ReleaseKey("project-1.0.0"),
        safe.RevisionNumber("00001"),
        allowed_vote_modes=frozenset({sql.VoteMode.MANUAL}),
    )
    assert result is not None and "archived" in result


@pytest.mark.asyncio
async def test_revision_clear_quarantine_blocks_retired() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=_query_returning(_retired_project()))
    writer = object.__new__(revision.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.clear_quarantine(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            42,
        )


@pytest.mark.asyncio
async def test_revision_create_revision_with_quarantine_blocks_retired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = object.__new__(revision.CommitteeParticipant)
    writer._CommitteeParticipant__data = mock.MagicMock()
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    inner_data = mock.MagicMock()
    inner_data.release = mock.MagicMock(return_value=_query_returning(_retired_release()))
    monkeypatch.setattr(revision.db, "session", lambda: _async_context_manager(inner_data))

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.create_revision_with_quarantine(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            "tester",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
        )


@pytest.mark.asyncio
async def test_sbom_augment_blocks_retired() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=_query_returning(_retired_project()))
    writer = object.__new__(sbom.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.augment_cyclonedx(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            safe.RelPath("artifact.tar.gz"),
        )


@pytest.mark.asyncio
async def test_vote_resolve_release_blocks_retired_after_merge() -> None:
    data = mock.MagicMock()
    merged = _retired_release()
    data.merge = mock.AsyncMock(return_value=merged)
    writer = object.__new__(vote.CommitteeMember)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__asf_uid = "tester"
    writer._ReleaseManager__committee_key = "project"

    stub_release = SimpleNamespace(
        project=SimpleNamespace(status=sql.ProjectStatus.ACTIVE, is_active=True, key="project")
    )
    with pytest.raises(storage.AccessError, match="archived"):
        await writer._ReleaseManager__resolve_release(
            safe.ProjectKey("project"),
            stub_release,
            None,
            "passed",
            SimpleNamespace(),
            "Chair",
            "body",
        )
    data.merge.assert_awaited_once_with(stub_release)


@pytest.mark.asyncio
async def test_vote_send_resolution_blocks_retired() -> None:
    writer = object.__new__(vote.CommitteeMember)
    writer._ReleaseManager__data = mock.MagicMock()
    writer._ReleaseManager__asf_uid = "tester"
    writer._ReleaseManager__committee_key = "project"

    rel = _retired_release()
    with pytest.raises(storage.AccessError, match="archived"):
        await writer._ReleaseManager__send_resolution(
            rel,
            "passed",
            "body",
            SimpleNamespace(),
        )


def _async_context_manager(value: object) -> mock.MagicMock:
    ctx = mock.MagicMock()
    ctx.__aenter__ = mock.AsyncMock(return_value=value)
    ctx.__aexit__ = mock.AsyncMock(return_value=None)
    return ctx


def _query_returning(obj: object) -> mock.MagicMock:
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=obj)
    query.get = mock.AsyncMock(return_value=obj)
    return query


def _retired_project(key: str = "project") -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        status=sql.ProjectStatus.RETIRED,
        is_active=False,
        committee_key=key,
        display_name=key.capitalize(),
        short_display_name=key.capitalize(),
    )


def _retired_release(project_key: str = "project", version: str = "1.0.0") -> SimpleNamespace:
    return SimpleNamespace(
        key=sql.release_key(project_key, version),
        project=_retired_project(project_key),
        version=version,
    )
