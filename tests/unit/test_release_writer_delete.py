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
import atr.storage.writers.release as release


class ReleaseQuery:
    def __init__(self, result: object) -> None:
        self._result = result

    async def demand(self, error: BaseException) -> object:
        if self._result is None:
            raise error
        return self._result


@pytest.mark.asyncio
async def test_delete_locks_before_reading_release(monkeypatch):
    participant, mock_data = _make_participant(_fake_release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT))
    delete_body = mock.AsyncMock(return_value=None)
    monkeypatch.setattr(release.CommitteeParticipant, "_CommitteeParticipant__delete_body", delete_body)

    error = await participant.delete(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))

    assert error is None
    delete_body.assert_awaited_once()
    names = [name for name, _args, _kwargs in mock_data.mock_calls]
    assert names.index("begin_immediate") < names.index("release")


@pytest.mark.asyncio
async def test_delete_refuses_announced_release(monkeypatch):
    participant, mock_data = _make_participant(_fake_release(sql.ReleasePhase.RELEASE))
    delete_body = mock.AsyncMock()
    monkeypatch.setattr(release.CommitteeParticipant, "_CommitteeParticipant__delete_body", delete_body)

    error = await participant.delete(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))

    assert error is not None
    assert "can only be archived" in error
    delete_body.assert_not_awaited()
    assert mock_data.rollback.await_count == 1


@pytest.mark.asyncio
async def test_delete_rolls_back_when_release_not_found():
    participant, mock_data = _make_participant(None)

    with pytest.raises(storage.AccessError, match="not found"):
        await participant.delete(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))

    assert mock_data.rollback.await_count == 1


def _fake_release(phase: sql.ReleasePhase) -> SimpleNamespace:
    return SimpleNamespace(phase=phase, project=SimpleNamespace(is_active=True))


def _make_participant(release_result: object) -> tuple[release.CommitteeParticipant, mock.MagicMock]:
    mock_data = mock.MagicMock()
    mock_data.attach_mock(mock.AsyncMock(), "begin_immediate")
    mock_data.attach_mock(mock.AsyncMock(), "rollback")
    mock_data.release.return_value = ReleaseQuery(release_result)
    mock_write = mock.MagicMock()
    mock_write.authorisation.asf_uid = "alice"
    participant = release.CommitteeParticipant(mock_write, mock.MagicMock(), mock_data, "test")
    return participant, mock_data
