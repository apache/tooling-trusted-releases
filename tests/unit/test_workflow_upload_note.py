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

import atr.get.vote as vote
import atr.models.safe as safe


@pytest.mark.asyncio
async def test_vote_page_shows_who_started_the_run_only_to_someone_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, made_by_workflow=True)

    signed_in = str(await vote._workflow_upload_note(_release(), signed_in=True))
    signed_out = str(await vote._workflow_upload_note(_release(), signed_in=False))

    assert "alice-gh" in signed_in
    assert "actions/runs/99" in signed_in
    assert "alice-gh" not in signed_out
    assert "actions/runs/99" not in signed_out
    # The repository and commit are public either way
    assert "apache/example" in signed_out
    assert "0123456789ab" in signed_out


@pytest.mark.asyncio
async def test_vote_page_says_nothing_when_the_voted_revision_is_not_a_workflow_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, made_by_workflow=False)

    assert await vote._workflow_upload_note(_release(), signed_in=True) is None


def _patch(monkeypatch: pytest.MonkeyPatch, *, made_by_workflow: bool) -> None:
    payload = SimpleNamespace(repository="apache/example", sha="0123456789abcdef" * 2 + "01234567", actor="alice-gh")
    payload.run_id = "99"
    read = mock.AsyncMock(return_value=payload if made_by_workflow else None)
    monkeypatch.setattr(vote.attestable, "github_tp_payload_read", read)


def _release() -> SimpleNamespace:
    return SimpleNamespace(
        latest_revision_number="00002",
        safe_latest_revision_number=safe.RevisionNumber("00002"),
        safe_project_key=safe.ProjectKey("example"),
        safe_version_key=safe.VersionKey("1.0.0"),
    )
