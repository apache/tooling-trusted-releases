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
import atr.shared.projects as shared_projects
import atr.storage as storage
import atr.storage.writers.announce as announce
import atr.storage.writers.distributions as distributions
import atr.storage.writers.policy as policy
import atr.storage.writers.project as project
import atr.storage.writers.workflowstatus as workflowstatus


class Query:
    def __init__(self, value: object) -> None:
        self.value = value

    async def demand(self, error: Exception) -> object:
        if self.value is None:
            raise error
        return self.value

    async def get(self) -> object:
        return self.value


def active_project(committee_key: str = "alpha") -> SimpleNamespace:
    return SimpleNamespace(
        key="example",
        status=sql.ProjectStatus.ACTIVE,
        committee_key=committee_key,
        release_policy=None,
    )


def release_row(committee_key: str = "alpha") -> SimpleNamespace:
    return SimpleNamespace(
        key="example-1.0.0",
        project=active_project(committee_key),
        project_key="example",
        version="1.0.0",
    )


@pytest.mark.asyncio
async def test_announce_release_rejects_foreign_committee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        announce.util,
        "permitted_announce_recipients",
        lambda _uid, committee_key: {"dev@example.apache.org"},
    )
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=Query(release_row("other")))
    writer = object.__new__(announce.ReleaseManager)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__asf_uid = "alice"
    writer._ReleaseManager__committee_key = "alpha"

    with pytest.raises(storage.AccessError, match="not in committee"):
        await writer.release(
            safe.ProjectKey("example"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            "dev@example.apache.org",
            "body",
            None,
            "Alice",
        )


@pytest.mark.asyncio
async def test_announce_release_reports_foreign_committee_before_retired_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        announce.util,
        "permitted_announce_recipients",
        lambda _uid, committee_key: {"dev@example.apache.org"},
    )
    foreign_retired_project = SimpleNamespace(
        key="example",
        status=sql.ProjectStatus.RETIRED,
        committee_key="other",
        release_policy=None,
    )
    release = SimpleNamespace(
        key="example-1.0.0",
        project=foreign_retired_project,
        project_key="example",
        version="1.0.0",
    )
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=Query(release))
    writer = object.__new__(announce.ReleaseManager)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__asf_uid = "alice"
    writer._ReleaseManager__committee_key = "alpha"

    with pytest.raises(storage.AccessError, match="not in committee"):
        await writer.release(
            safe.ProjectKey("example"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            "dev@example.apache.org",
            "body",
            None,
            "Alice",
        )


@pytest.mark.asyncio
async def test_distribution_record_rejects_foreign_committee() -> None:
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=Query(release_row("other")))
    writer = object.__new__(distributions.ReleaseManager)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__asf_uid = "alice"
    writer._ReleaseManager__committee_key = "alpha"

    with pytest.raises(storage.AccessError, match="not in committee"):
        await writer.record(
            safe.ReleaseKey("example-1.0.0"),
            sql.DistributionPlatform.MAVEN,
            None,
            safe.Alphanumeric("pkg"),
            safe.VersionKey("1.0.0"),
            False,
            False,
            None,
        )


@pytest.mark.asyncio
async def test_policy_edit_rejects_foreign_committee() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=Query(active_project("other")))
    writer = object.__new__(policy.ReleaseManager)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__asf_uid = "alice"
    writer._ReleaseManager__committee_key = "alpha"
    form = SimpleNamespace(
        project_key=safe.ProjectKey("example"),
        version_method=sql.VersionMethod.SIMPLE,
        version_pattern="",
        cycle_match="",
        branch_template="",
    )

    with pytest.raises(storage.AccessError, match="not in committee"):
        await writer.edit_version_scheme(form)


@pytest.mark.asyncio
async def test_project_metadata_rejects_foreign_committee() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=Query(active_project("other")))
    writer = object.__new__(project.ReleaseManager)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__asf_uid = "alice"
    writer._ReleaseManager__committee_key = "alpha"
    form = shared_projects.EditMetadataForm(
        variant="edit_metadata",
        csrf_token="test",
        project_key=safe.ProjectKey("example"),
        display_name="Example",
        description="",
        short_description="",
        homepage="",
        lifecycle_page="",
        download_page="",
        bug_database="",
        mailing_lists="",
        repositories=[],
        standards=[],
    )

    with pytest.raises(storage.AccessError, match="not in committee"):
        await writer.edit_metadata(form)


def test_release_manager_surface_exposes_only_approved_committee_member_writers() -> None:
    warm = write_as_release_manager()

    assert hasattr(warm.announce, "release")
    assert hasattr(warm.distributions, "automate")
    assert hasattr(warm.distributions, "delete_distribution")
    assert hasattr(warm.distributions, "record")
    assert hasattr(warm.distributions, "record_from_data")
    assert hasattr(warm.policy, "edit_vote")
    assert hasattr(warm.project, "edit_metadata")
    assert hasattr(warm.project, "set_download_page")
    assert hasattr(warm.release, "promote_to_candidate")
    assert hasattr(warm.vote, "resolve")
    assert hasattr(warm.vote, "start")
    assert hasattr(warm.workflowstatus, "add_workflow_status")

    assert not hasattr(warm, "committee")
    assert not hasattr(warm.checks, "ignore_add")
    assert not hasattr(warm.project, "archive")
    assert not hasattr(warm.project, "category_add")
    assert not hasattr(warm.project, "delete")
    assert not hasattr(warm.project, "language_add")
    assert not hasattr(warm.project, "upsert_config")
    assert not hasattr(warm.release, "archive")


@pytest.mark.asyncio
async def test_set_download_page_rejects_http_and_audits() -> None:
    write_as = mock.MagicMock()
    writer = object.__new__(project.ReleaseManager)
    writer._ReleaseManager__asf_uid = "alice"
    writer._ReleaseManager__write_as = write_as

    with pytest.raises(storage.AccessError, match="https"):
        await writer.set_download_page(safe.ProjectKey("example"), "http://example.apache.org/download")

    write_as.append_to_audit_log.assert_called_once_with(
        asf_uid="alice",
        project_key="example",
        download_page="http://example.apache.org/download",
        rejected="Must use https",
    )


@pytest.mark.asyncio
async def test_workflow_status_rejects_foreign_committee() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=Query(active_project("other")))
    writer = object.__new__(workflowstatus.ReleaseManager)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__asf_uid = "alice"
    writer._ReleaseManager__committee_key = "alpha"

    with pytest.raises(storage.AccessError, match="not in committee"):
        await writer.add_workflow_status("workflow.yml", 123, safe.ProjectKey("example"))


def write_as_release_manager() -> storage.WriteAsReleaseManager:
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    data = mock.MagicMock()
    return storage.WriteAsReleaseManager(write, data, "alpha")
