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

import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.readers.releases as releases
import atr.storage.types as types


def test_checker_stats_counts_and_files_by_status():
    path = safe.RelPath("apache-test-1.0-source.tar.gz")
    success = _make_check_result(sql.CheckResultStatus.SUCCESS, "Success", primary_rel_path=str(path))
    warning = _make_check_result(sql.CheckResultStatus.WARNING, "Warning", primary_rel_path=str(path))
    failure = _make_check_result(sql.CheckResultStatus.FAILURE, "Failure", primary_rel_path=str(path))
    blocker = _make_check_result(sql.CheckResultStatus.BLOCKER, "Blocker", primary_rel_path=str(path))
    info = types.PathInfo(
        successes={path: [success]},
        warnings={path: [warning]},
        errors={path: [failure, blocker]},
    )
    reader = _make_reader()
    compute_checker_stats = getattr(reader, "_GeneralPublic__compute_checker_stats")

    compute_checker_stats(info, [path])

    assert len(info.checker_stats) == 1
    stat = info.checker_stats[0]
    assert stat.checker == "atr.tasks.checks.paths.check_errors"
    assert stat.counts == collections.Counter(
        {
            sql.CheckResultStatus.SUCCESS: 1,
            sql.CheckResultStatus.WARNING: 1,
            sql.CheckResultStatus.FAILURE: 1,
            sql.CheckResultStatus.BLOCKER: 1,
        }
    )
    assert stat.files == {
        sql.CheckResultStatus.WARNING: {str(path): 1},
        sql.CheckResultStatus.FAILURE: {str(path): 1},
        sql.CheckResultStatus.BLOCKER: {str(path): 1},
    }


def test_exceptions_bucketed_into_path_info():
    path = safe.RelPath("apache-test-1.0-source.tar.gz")
    exception = _make_check_result(sql.CheckResultStatus.EXCEPTION, "Error", primary_rel_path=str(path))
    release_level = _make_check_result(sql.CheckResultStatus.EXCEPTION, "Tooling failure", primary_rel_path=None)
    info = types.PathInfo()
    subset = types.ChecksSubset(checks=[exception, release_level], info=info, match_ignore=lambda _: False)
    reader = _make_reader()
    bucket_exceptions = getattr(reader, "_GeneralPublic__exceptions")

    asyncio.run(bucket_exceptions(subset))

    assert info.exceptions[path] == [exception]
    assert path not in info.errors
    assert info.release_level_exceptions == [release_level]
    assert info.release_level_errors == []
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
