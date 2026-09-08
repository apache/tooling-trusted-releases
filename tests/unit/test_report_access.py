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

import collections
import pathlib
import types
import unittest.mock as mock

import asfquart.base as base
import pytest
import quart

import atr.get.report as report
import atr.models.safe as safe
import atr.models.sql as sql
import atr.sessions as sessions
import atr.storage.datatypes as datatypes
import atr.storage.readers.checks as checks


@pytest.mark.parametrize("embargoed", [False, True])
async def test_report_anonymous_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, embargoed: bool
) -> None:
    release = types.SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        committee=sql.Committee(key="test"),
        is_embargoed=embargoed,
        latest_revision_number="00001",
        safe_latest_revision_number=safe.RevisionNumber("00001"),
    )
    data = mock.MagicMock()
    data.release.return_value.get = mock.AsyncMock(return_value=release)
    data.release.return_value.demand = mock.AsyncMock(return_value=release)
    context = mock.AsyncMock()
    context.__aenter__.return_value = data
    results = datatypes.CheckResults([], {}, [], 0, collections.Counter(), 0)
    render = mock.AsyncMock(return_value="report")
    monkeypatch.setattr(report.db, "session", lambda: context)
    monkeypatch.setattr(sessions, "read", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(report.paths, "release_directory", lambda _: tmp_path)
    monkeypatch.setattr(report.attestable, "load", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(checks.GeneralPublic, "by_release_path", mock.AsyncMock(return_value=results))
    monkeypatch.setattr(report.template, "render", render)
    (tmp_path / "source.tar.gz").write_bytes(b"source")

    app = quart.Quart(__name__)
    async with app.test_request_context("/report/test/0.1/source.tar.gz"):
        if not embargoed:
            response = await report.selected_path(project_key="test", version_key="0.1", rel_path="source.tar.gz")
            assert response == "report"
            return
        with pytest.raises(base.ASFQuartException) as error:
            await report.selected_path(project_key="test", version_key="0.1", rel_path="source.tar.gz")
        assert error.value.errorcode == 404
        render.assert_not_awaited()
