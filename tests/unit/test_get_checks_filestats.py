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
import collections.abc
import contextlib
import datetime
import unittest.mock as mock

import pytest

import atr.get.checks as checks
import atr.models.safe as safe
import atr.models.sql as sql


async def test_compute_stats_counts_by_status_before_and_after(monkeypatch: pytest.MonkeyPatch) -> None:
    path = safe.RelPath("apache-test-1.0-source.tar.gz")
    check_results = [
        _make_check_result(sql.CheckResultStatus.NOTE, "Success", str(path)),
        _make_check_result(sql.CheckResultStatus.SUGGESTION, "Warning", str(path)),
        _make_check_result(sql.CheckResultStatus.CONCERN, "Failure", str(path)),
        _make_check_result(sql.CheckResultStatus.BLOCKER, "Blocker", str(path), "apache-test-1.0/pom.xml"),
        _make_check_result(sql.CheckResultStatus.EXCEPTION, "Exception", str(path), "apache-test-1.0/build.xml"),
    ]
    release = mock.MagicMock()
    release.latest_revision_number = "00001"

    monkeypatch.setattr(checks.db, "session", _mock_db_session)
    monkeypatch.setattr(checks.interaction, "checks_for", mock.AsyncMock(return_value=check_results))

    per_file, totals = await checks._compute_stats(release, [path], _match_ignore)
    stats = per_file[path]

    assert stats.file_before == collections.Counter(
        {
            sql.CheckResultStatus.NOTE: 1,
            sql.CheckResultStatus.SUGGESTION: 1,
            sql.CheckResultStatus.CONCERN: 1,
        }
    )
    assert stats.file_after == collections.Counter(
        {
            sql.CheckResultStatus.NOTE: 1,
            sql.CheckResultStatus.CONCERN: 1,
        }
    )
    assert stats.member_before == collections.Counter(
        {
            sql.CheckResultStatus.BLOCKER: 1,
            sql.CheckResultStatus.EXCEPTION: 1,
        }
    )
    assert stats.member_after == collections.Counter({sql.CheckResultStatus.BLOCKER: 1})
    assert totals.file_before == stats.file_before
    assert totals.file_after == stats.file_after
    assert totals.member_before == stats.member_before
    assert totals.member_after == stats.member_after
    assert totals.total_before(sql.CheckResultStatus.EXCEPTION) == 1
    assert totals.total_after(sql.CheckResultStatus.EXCEPTION) == 0


def _make_check_result(
    status: sql.CheckResultStatus,
    message: str,
    primary_rel_path: str,
    member_rel_path: str | None = None,
) -> sql.CheckResult:
    return sql.CheckResult(
        id=0,
        release_key="test-1.0",
        revision_number="00001",
        checker="atr.tasks.checks.paths.check_errors",
        checker_version="3",
        primary_rel_path=primary_rel_path,
        member_rel_path=member_rel_path,
        created=datetime.datetime.now(datetime.UTC),
        status=status,
        message=message,
        data={},
        inputs_hash=None,
    )


def _match_ignore(check_result: sql.CheckResult) -> bool:
    return check_result.status in (
        sql.CheckResultStatus.NOTE,
        sql.CheckResultStatus.SUGGESTION,
        sql.CheckResultStatus.EXCEPTION,
    )


@contextlib.asynccontextmanager
async def _mock_db_session() -> collections.abc.AsyncGenerator[mock.MagicMock]:
    yield mock.MagicMock()
