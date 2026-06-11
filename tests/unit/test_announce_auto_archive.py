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
import atr.storage.writers.announce as announce


def announced_release(*, release_opt_in: bool = True, policy_opt_in: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        archive_prior_release=release_opt_in,
        project=SimpleNamespace(
            key="example",
            policy_auto_archive_prior_release=policy_opt_in,
        ),
        project_key="example",
        safe_project_key=safe.ProjectKey("example"),
        version="2.0.0",
    )


def prior_release() -> SimpleNamespace:
    return SimpleNamespace(
        project_key="example",
        safe_version_key=safe.VersionKey("1.0.0"),
        version="1.0.0",
    )


@pytest.mark.asyncio
async def test_auto_archive_archives_server_resolved_prior_release(monkeypatch: pytest.MonkeyPatch) -> None:
    prior = prior_release()
    archive = mock.AsyncMock(return_value=None)
    resolver = mock.AsyncMock(return_value=prior)
    monkeypatch.setattr(announce.interaction, "prior_release_for_archive", resolver)
    release_manager = writer()
    release_manager._ReleaseManager__archive_release = archive

    await release_manager._ReleaseManager__archive_prior_release(announced_release(), True)

    resolver.assert_awaited_once()
    archive.assert_awaited_once_with(
        safe.ProjectKey("example"),
        safe.VersionKey("1.0.0"),
        prior,
    )


@pytest.mark.asyncio
async def test_auto_archive_ignores_form_opt_in_when_release_did_not_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = mock.AsyncMock(return_value=None)
    resolver = mock.AsyncMock(return_value=prior_release())
    monkeypatch.setattr(announce.interaction, "prior_release_for_archive", resolver)
    release_manager = writer()
    release_manager._ReleaseManager__archive_release = archive

    await release_manager._ReleaseManager__archive_prior_release(announced_release(release_opt_in=False), True)

    resolver.assert_not_awaited()
    archive.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_archive_ignores_stale_form_opt_in_when_policy_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = mock.AsyncMock(return_value=None)
    resolver = mock.AsyncMock(return_value=prior_release())
    monkeypatch.setattr(announce.interaction, "prior_release_for_archive", resolver)
    release_manager = writer()
    release_manager._ReleaseManager__archive_release = archive

    await release_manager._ReleaseManager__archive_prior_release(announced_release(policy_opt_in=False), True)

    resolver.assert_not_awaited()
    archive.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_archive_reports_archive_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(announce.interaction, "prior_release_for_archive", mock.AsyncMock(return_value=prior_release()))
    release_manager = writer()
    release_manager._ReleaseManager__archive_release = mock.AsyncMock(return_value="already archived")

    with pytest.raises(storage.AccessError, match="Release announced, but archiving prior release"):
        await release_manager._ReleaseManager__archive_prior_release(announced_release(), True)


def writer() -> announce.ReleaseManager:
    release_manager = object.__new__(announce.ReleaseManager)
    release_manager._ReleaseManager__data = mock.MagicMock()
    release_manager._ReleaseManager__write_as = mock.MagicMock()
    release_manager._ReleaseManager__asf_uid = "alice"
    release_manager._ReleaseManager__committee_key = "alpha"
    return release_manager
