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

from __future__ import annotations

import contextlib
import unittest.mock as mock
from typing import TYPE_CHECKING, Any

import pytest

import atr.models.safe as safe
import atr.post.projects as projects
import atr.storage as storage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


class DenyingWrite:
    async def as_project_committee_member(self, project_key: safe.ProjectKey) -> None:
        raise storage.AccessError(f"Not a member of {project_key}", status=403)

    async def as_project_release_manager(self, project_key: safe.ProjectKey) -> None:
        raise storage.AccessError(f"Not a release manager for {project_key}", status=403)


@contextlib.asynccontextmanager
async def _denying_write(_session: Any) -> AsyncIterator[DenyingWrite]:
    yield DenyingWrite()


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (projects._process_add_category, "Not a member of"),
        (projects._process_compose_form, "Not a release manager for"),
    ],
)
async def test_denied_credential_redirects(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[Any, Any], Awaitable[Any]], message: str
) -> None:
    monkeypatch.setattr(projects.storage, "write", _denying_write)
    session = mock.Mock(is_admin=False)
    session.redirect = mock.AsyncMock(return_value="redirected")
    form = mock.Mock(project_key=safe.ProjectKey("httpd-foo"), category_to_add="Web")
    assert await handler(session, form) == "redirected"
    assert message in session.redirect.await_args.kwargs["error"]


@pytest.mark.parametrize("handler", [projects._process_add_category, projects._process_compose_form])
async def test_admin_uses_committee_admin(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[Any, Any], Awaitable[Any]]
) -> None:
    write = mock.AsyncMock()
    monkeypatch.setattr(projects.storage, "write", lambda _session: contextlib.nullcontext(write))
    session = mock.Mock(is_admin=True)
    session.redirect = mock.AsyncMock(return_value="redirected")
    form = mock.Mock(project_key=safe.ProjectKey("httpd-foo"), category_to_add="Web")
    assert await handler(session, form) == "redirected"
    write.as_project_committee_admin.assert_awaited_once_with(safe.ProjectKey("httpd-foo"))
    assert "success" in session.redirect.await_args.kwargs
