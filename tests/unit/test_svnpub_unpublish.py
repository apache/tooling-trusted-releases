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

import contextlib
import unittest.mock as mock
from collections.abc import AsyncIterator
from typing import Any

import pytest

import atr.models.args as args
import atr.models.results as results
import atr.storage.datatypes as datatypes
import atr.tasks.svnpub as svnpub
import atr.tasks.task as task


def _task_args() -> dict[str, Any]:
    return args.SvnUnpublish(asf_uid="alice", project_key="project", version_key="1.0.0").model_dump()


def _write_as_system(service: mock.Mock) -> Any:
    @contextlib.asynccontextmanager
    async def _cm(_service_cls: Any) -> AsyncIterator[mock.Mock]:
        yield service

    return _cm


async def test_unpublish_defers_a_retryable_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    service = mock.Mock()
    service.release_unpublish_from_svn = mock.AsyncMock(side_effect=datatypes.RetryableError("out of date"))
    service.revert_failed_archive = mock.AsyncMock()
    monkeypatch.setattr(svnpub.storage, "write_as_system", _write_as_system(service))
    notify_user = mock.AsyncMock()
    monkeypatch.setattr(svnpub.notify, "user", notify_user)

    with pytest.raises(task.DeferredError):
        await svnpub.unpublish(_task_args())

    # A deferral leaves the archival alone and says nothing to the user - it will try again
    service.revert_failed_archive.assert_not_awaited()
    notify_user.assert_not_awaited()


async def test_unpublish_reverts_and_notifies_on_a_terminal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    service = mock.Mock()
    service.release_unpublish_from_svn = mock.AsyncMock(side_effect=datatypes.FailedError("gone for good"))
    service.revert_failed_archive = mock.AsyncMock()
    monkeypatch.setattr(svnpub.storage, "write_as_system", _write_as_system(service))
    notify_user = mock.AsyncMock()
    monkeypatch.setattr(svnpub.notify, "user", notify_user)

    # The task still records a failure, but the release is put back and the archiver told
    with pytest.raises(datatypes.FailedError):
        await svnpub.unpublish(_task_args())

    service.revert_failed_archive.assert_awaited_once()
    notify_user.assert_awaited_once()


async def test_unpublish_returns_the_result_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    result = results.SvnUnpublish(kind="svn_unpublish", svn_revision=7, message="done")
    service = mock.Mock()
    service.release_unpublish_from_svn = mock.AsyncMock(return_value=result)
    service.revert_failed_archive = mock.AsyncMock()
    monkeypatch.setattr(svnpub.storage, "write_as_system", _write_as_system(service))
    notify_user = mock.AsyncMock()
    monkeypatch.setattr(svnpub.notify, "user", notify_user)

    assert await svnpub.unpublish(_task_args()) is result
    service.revert_failed_archive.assert_not_awaited()
    notify_user.assert_not_awaited()
