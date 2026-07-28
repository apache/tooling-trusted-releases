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

import atr.models.safe as safe
import atr.models.sql as sql
import atr.render as render
import atr.storage.datatypes as datatypes


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


def test_render_checks_summary_shows_release_level_exception() -> None:
    result = mock.MagicMock(spec=sql.CheckResult)
    result.status = sql.CheckResultStatus.EXCEPTION
    result.message = "Base release directory does not exist"
    result.checker = "atr.tasks.checks.paths.check"
    info = datatypes.PathInfo()
    info.release_level_exceptions.append(result)

    card = render.render_checks_summary(info, safe.ProjectKey("test"), safe.VersionKey("1.0"))

    assert card is not None
    html = str(card)
    assert "Checks summary" in html
    assert "Base release directory does not exist" in html


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
