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

import asfquart.base as base
import pytest
import quart

import atr.get.start as start
import atr.models.safe as safe
import atr.models.sql as sql


@pytest.fixture
def preview(monkeypatch):
    app = quart.Quart(__name__)
    app.config["TESTING"] = True
    app.register_error_handler(base.ASFQuartException, lambda error: ("", error.errorcode))
    app.register_blueprint(start.get._BLUEPRINT)
    data = mock.Mock()
    data.project.return_value.demand = mock.AsyncMock()
    session = mock.Mock()
    session.prevent_confusing_ui_display = mock.AsyncMock()
    monkeypatch.setattr(start.db, "session", lambda: contextlib.nullcontext(data))
    monkeypatch.setattr(start.get.auth.session, "read", mock.AsyncMock(return_value=object()))
    monkeypatch.setattr(start.get.common, "authenticate", mock.AsyncMock(return_value=session))
    monkeypatch.setattr(start.get.log, "performance", mock.Mock())
    return app.test_client(), data, session


@pytest.mark.parametrize("version", ["", "a" * 129, "1.2.", "\x1e2.5.3\x1e", "\ufeff2.5.3\ufeff"])
async def test_cycle_preview_invalid_version(preview, monkeypatch, version):
    client, _data, _session = preview
    resolver = mock.Mock()
    monkeypatch.setattr(start.cycles, "cycle_name_for_version", resolver)
    response = await client.get("/start/example/cycle-preview", query_string={"version": version})
    assert response.status_code == 200
    assert await response.get_json() == {"cycle": None}
    resolver.assert_not_called()


async def test_cycle_preview_missing_project(preview):
    client, data, _session = preview
    data.project.return_value.demand.side_effect = base.ASFQuartException("Missing project", errorcode=404)
    response = await client.get("/start/example/cycle-preview?version=2.5.3")
    assert response.status_code == 404


async def test_cycle_preview_missing_version(preview, monkeypatch):
    client, _data, _session = preview
    resolver = mock.Mock()
    monkeypatch.setattr(start.cycles, "cycle_name_for_version", resolver)
    response = await client.get("/start/example/cycle-preview")
    assert response.status_code == 200
    assert await response.get_json() == {"cycle": None}
    resolver.assert_not_called()


@pytest.mark.parametrize(
    ("pattern", "version", "cycle"),
    [
        ("(a|aa)", "aa", "aa"),
        (r"(?P<cycle>\d+)\..*", "2.5.3", "2"),
        ("^(a+)+$", ("a" * 30) + "X", None),
        ("(a*)b", "b", None),
        ("(a)?b", "b", None),
        ("a", "a", None),
        ("(", "a", None),
        (None, "2.5.3", "default"),
        (r"(\d+)\..*", " \t2.5.3\u00a0", "2"),
        (r"(\d+)\..*", "2.5.3+build", "2"),
    ],
)
async def test_cycle_preview_resolves(preview, pattern, version, cycle):
    client, data, session = preview
    data.project.return_value.demand.return_value = sql.Project(key="example", cycle_match=pattern)
    response = await client.get("/start/example/cycle-preview", query_string={"version": version})
    assert response.status_code == 200
    assert await response.get_json() == {"cycle": cycle}
    data.project.assert_called_once_with(key="example", status=sql.ProjectStatus.ACTIVE, _committee=False)
    session.prevent_confusing_ui_display.assert_awaited_once_with(safe.ProjectKey("example"), flash_admin_warning=False)


async def test_cycle_preview_unauthenticated(preview, monkeypatch):
    client, data, _session = preview
    monkeypatch.setattr(start.get.auth.session, "read", mock.AsyncMock(return_value=None))
    response = await client.get("/start/example/cycle-preview?version=2.5.3")
    assert response.status_code == 403
    data.project.assert_not_called()


async def test_cycle_preview_unauthorized(preview):
    client, data, session = preview
    session.prevent_confusing_ui_display.side_effect = base.ASFQuartException("Forbidden", errorcode=403)
    response = await client.get("/start/example/cycle-preview?version=2.5.3")
    assert response.status_code == 403
    data.project.assert_not_called()


async def test_cycle_preview_url(preview):
    client, _data, _session = preview
    async with client.app.test_request_context("/"):
        html = str(start._cycle_preview(sql.Project(key="example", cycle_match="^(a+)+$")))
    assert 'data-preview-url="/start/example/cycle-preview"' in html
    assert "^(a+)+$" not in html
    assert 'id="start-cycle-preview-text"' in html
    assert "?tab=lifecycle" in html
    assert start._cycle_preview(sql.Project(key="example", cycle_match=None)) is None
