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

import asyncio
import collections
import datetime
import unittest.mock as mock

import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.datatypes as datatypes
import atr.storage.readers.releases as releases


def test_checker_stats_counts_and_files_by_status():
    path = safe.RelPath("apache-test-1.0-source.tar.gz")
    warning = _make_check_result(sql.CheckResultStatus.SUGGESTION, "Warning", primary_rel_path=str(path))
    failure = _make_check_result(sql.CheckResultStatus.CONCERN, "Failure", primary_rel_path=str(path))
    blocker = _make_check_result(sql.CheckResultStatus.BLOCKER, "Blocker", primary_rel_path=str(path))
    info = datatypes.PathInfo(
        note_counts={path: 1},
        suggestions={path: [warning]},
        concerns={path: [failure]},
        blockers={path: [blocker]},
    )
    notes = [
        interaction.CheckCount(str(path), "atr.tasks.checks.paths.check_errors", sql.CheckResultStatus.NOTE, False, 1)
    ]
    reader = _make_reader()
    compute_checker_stats = getattr(reader, "_GeneralPublic__compute_checker_stats")

    compute_checker_stats(info, [path], notes)

    assert len(info.checker_stats) == 1
    stat = info.checker_stats[0]
    assert stat.checker == "atr.tasks.checks.paths.check_errors"
    assert stat.counts == collections.Counter(
        {
            sql.CheckResultStatus.NOTE: 1,
            sql.CheckResultStatus.SUGGESTION: 1,
            sql.CheckResultStatus.CONCERN: 1,
            sql.CheckResultStatus.BLOCKER: 1,
        }
    )
    assert stat.files == {
        sql.CheckResultStatus.SUGGESTION: {str(path): 1},
        sql.CheckResultStatus.CONCERN: {str(path): 1},
        sql.CheckResultStatus.BLOCKER: {str(path): 1},
    }


def test_exceptions_bucketed_into_path_info():
    path = safe.RelPath("apache-test-1.0-source.tar.gz")
    exception = _make_check_result(sql.CheckResultStatus.EXCEPTION, "Error", primary_rel_path=str(path))
    release_level = _make_check_result(sql.CheckResultStatus.EXCEPTION, "Tooling failure", primary_rel_path=None)
    info = datatypes.PathInfo()
    subset = datatypes.ChecksSubset(checks=[exception, release_level], info=info, match_ignore=lambda _: False)
    reader = _make_reader()
    bucket_exceptions = getattr(reader, "_GeneralPublic__exceptions")

    asyncio.run(bucket_exceptions(subset))

    assert info.exceptions[path] == [exception]
    assert path not in info.concerns
    assert info.release_level_exceptions == [release_level]
    assert info.release_level_concerns == []
    assert info.ignored_exceptions == []


def _make_check_result(
    status: sql.CheckResultStatus,
    message: str,
    primary_rel_path: str | None = None,
) -> sql.CheckResult:
    return sql.CheckResult(
        id=0,
        release_key="test-1.0",
        revision_number="00001",
        checker="atr.tasks.checks.paths.check_errors",
        checker_version="3",
        primary_rel_path=primary_rel_path,
        member_rel_path=None,
        created=datetime.datetime.now(datetime.UTC),
        status=status,
        message=message,
        data={},
        inputs_hash=None,
    )


def _make_reader() -> releases.GeneralPublic:
    read = mock.MagicMock()
    read.authorisation.asf_uid = "testuser"
    read_as = mock.MagicMock()
    data = mock.AsyncMock()
    return releases.GeneralPublic(read, read_as, data)
