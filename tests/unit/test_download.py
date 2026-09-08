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

import pytest
import quart

import atr.blueprints.common as common
import atr.get.download as download
import atr.models.safe as safe
import atr.models.sql as sql


@pytest.mark.parametrize(
    "phase",
    [sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, sql.ReleasePhase.RELEASE_CANDIDATE, sql.ReleasePhase.RELEASE_PREVIEW],
)
async def test_path_empty_lists_release_root(monkeypatch, tmp_path, phase):
    (tmp_path / "README.txt").write_text("Release files")
    (tmp_path / "src").mkdir()
    release = sql.Release(project_key="test", version="0.1", phase=phase)
    data = mock.MagicMock()
    data.__aenter__.return_value = data
    data.release.return_value.demand = mock.AsyncMock(return_value=release)
    data.release.return_value.get = mock.AsyncMock(return_value=release)
    monkeypatch.setattr(common, "authenticate_public", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(download.db, "session", lambda: data)
    monkeypatch.setattr(download.paths, "release_directory", lambda _: safe.StatePath(tmp_path))
    app = quart.Quart(__name__)
    app.config["TESTING"] = True
    app.add_url_rule(
        "/download/path/<project_key>/<version_key>",
        endpoint=download.path_empty.endpoint,
        view_func=download.path_empty,
    )
    app.add_url_rule(
        "/download/path/<project_key>/<version_key>/<path:file_path>",
        endpoint=download.path.endpoint,
        view_func=download.path,
    )

    response = await app.test_client().get("/download/path/test/0.1")

    assert response.status_code == 200
    body = await response.get_data(as_text=True)
    assert '<a href="/download/path/test/0.1/README.txt">README.txt</a>' in body
    assert '<a href="/download/path/test/0.1/src">src/</a>' in body
    assert "../" not in body
