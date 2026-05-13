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

import datetime
import unittest.mock as mock

import atr.models.safe as safe
import atr.models.sql as sql
import atr.render as render
import atr.storage.types as types


def test_highest_severity_precedence() -> None:
    assert render.highest_severity({}) is None
    assert render.highest_severity({sql.CheckResultStatus.NOTE: 1}) == sql.CheckResultStatus.NOTE
    assert (
        render.highest_severity({sql.CheckResultStatus.SUGGESTION: 1, sql.CheckResultStatus.NOTE: 1})
        == sql.CheckResultStatus.SUGGESTION
    )
    assert (
        render.highest_severity({sql.CheckResultStatus.CONCERN: 1, sql.CheckResultStatus.SUGGESTION: 1})
        == sql.CheckResultStatus.CONCERN
    )
    assert (
        render.highest_severity({sql.CheckResultStatus.EXCEPTION: 1, sql.CheckResultStatus.CONCERN: 1})
        == sql.CheckResultStatus.EXCEPTION
    )
    assert (
        render.highest_severity({sql.CheckResultStatus.BLOCKER: 1, sql.CheckResultStatus.EXCEPTION: 1})
        == sql.CheckResultStatus.BLOCKER
    )


def test_render_exception_banner_empty_returns_none() -> None:
    info = types.PathInfo()
    assert render.render_exception_banner(info) is None


def test_render_exception_banner_path_level() -> None:
    path = safe.RelPath("apache-test-1.0-source.tar.gz")
    info = types.PathInfo()
    info.exceptions[path] = [_fake_check_result()]
    banner = render.render_exception_banner(info)
    assert banner is not None
    html = str(banner)
    assert "atr-bg-exception" in html
    assert "could not complete 1 automated check" in html
    assert str(path) in html


def test_render_exception_banner_release_level_only() -> None:
    info = types.PathInfo()
    info.release_level_exceptions.append(_fake_check_result())
    banner = render.render_exception_banner(info)
    assert banner is not None
    html = str(banner)
    assert "release-level exception" in html


def test_table_constants_are_consistent() -> None:
    assert sql.CheckResultStatus.NOTE in render.HIDDEN_STATUSES
    assert sql.CheckResultStatus.EXCEPTION == render.BANNER_STATUS
    assert set(render.TABLE_STATUSES) == {
        sql.CheckResultStatus.SUGGESTION,
        sql.CheckResultStatus.CONCERN,
        sql.CheckResultStatus.BLOCKER,
    }
    for status in render.TABLE_STATUSES:
        assert status in render.COLUMN_HEADERS
        assert status in render.CELL_TEXT_CLASS
    assert render.PATH_STYLE_CLASS[sql.CheckResultStatus.BLOCKER] == "atr-text-blocker"
    assert render.PATH_STYLE_CLASS[sql.CheckResultStatus.EXCEPTION] == "text-danger"


def _fake_check_result() -> sql.CheckResult:
    result = mock.MagicMock(spec=sql.CheckResult)
    result.status = sql.CheckResultStatus.EXCEPTION
    result.message = "tool crashed"
    result.created = datetime.datetime(2026, 5, 13, tzinfo=datetime.UTC)
    result.checker = "atr.tasks.checks.foo"
    return result
