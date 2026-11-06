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

import asfquart.base as base
import pytest

import atr.get.distribution as distribution
import atr.models.safe as safe
import atr.models.sql as sql
import atr.ssh as ssh
import atr.storage as storage
import atr.storage.writers.distributions as distributions

DRAFT = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
PREVIEW = sql.ReleasePhase.RELEASE_PREVIEW
PROJECT = safe.ProjectKey("example")
VERSION = safe.VersionKey("1.0.0")
VOTE = sql.ReleasePhase.RELEASE_CANDIDATE


@pytest.mark.parametrize(
    ("read_only", "phase", "allowed"),
    [(True, DRAFT, False), (True, VOTE, False), (True, PREVIEW, True), (False, DRAFT, True)],
)
async def test_embargoed_download_is_refused_to_workflows_before_finish(
    monkeypatch: pytest.MonkeyPatch, read_only: bool, phase: sql.ReleasePhase, allowed: bool
) -> None:
    monkeypatch.setattr("atr.user.embargo_denies", lambda *_args, **_kwargs: False)
    data = mock.MagicMock()
    data.project.return_value.get = mock.AsyncMock(return_value=mock.MagicMock())
    data.release.return_value.get = mock.AsyncMock(return_value=_release(True, phase))
    session = mock.AsyncMock()
    session.__aenter__.return_value = data
    monkeypatch.setattr("atr.db.session", lambda: session)
    server = mock.MagicMock(_github_read_only=read_only)
    server._get_github_project_key.return_value = PROJECT
    server._get_asf_uid.return_value = "alice"
    args = [mock.MagicMock(), [".", f"/{PROJECT}/{VERSION}/"], True, server]
    if allowed:
        await ssh._step_04_command_validate(*args)
        return
    with pytest.raises(ssh.RsyncArgsError, match="embargoed"):
        await ssh._step_04_command_validate(*args)


async def test_embargoed_staging_form_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    release_validated = mock.AsyncMock(return_value=_release(True, DRAFT))
    monkeypatch.setattr("atr.shared.distribution.release_validated", release_validated)
    with pytest.raises(base.ASFQuartException) as exception:
        await distribution._automate_form_page(PROJECT, VERSION, staging=True)
    assert exception.value.errorcode == 403


@pytest.mark.parametrize(
    ("staging", "expedited", "allowed"),
    [(True, True, False), (False, True, True), (True, False, True)],
)
async def test_embargoed_staging_is_not_queued(staging: bool, expedited: bool, allowed: bool) -> None:
    release = _release(expedited, DRAFT if staging else PREVIEW)
    release.project = mock.MagicMock(committee_key="example", is_active=True)
    data = mock.MagicMock(commit=mock.AsyncMock(), refresh=mock.AsyncMock())
    data.release.return_value.demand = mock.AsyncMock(return_value=release)
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    manager = distributions.ReleaseManager(write, mock.MagicMock(), data, "example")
    automate = manager.automate(
        release.key,
        sql.DistributionPlatform.MAVEN,
        "example",
        safe.OwnerNamespace("org.apache.example"),
        PROJECT,
        VERSION,
        "compose",
        None,
        safe.Alphanumeric("example"),
        VERSION,
        staging,
    )
    if allowed:
        await automate
        data.add.assert_called_once()
        return
    with pytest.raises(storage.AccessError, match="Embargoed"):
        await automate
    data.add.assert_not_called()


def _release(expedited: bool, phase: sql.ReleasePhase) -> sql.Release:
    return sql.Release(key="example-1.0.0", project_key="example", version="1.0.0", phase=phase, expedited=expedited)
