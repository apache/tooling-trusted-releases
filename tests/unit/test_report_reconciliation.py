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
import datetime

import atr.get.report as report
import atr.models.sql as sql


def test_reconciliation_empty_without_member_results() -> None:
    primary = [_check_result(sql.CheckResultStatus.CONCERN)]
    assert report._reconciliation(primary, collections.Counter()) == ""


def test_reconciliation_splits_primary_and_member_counts() -> None:
    primary = [
        _check_result(sql.CheckResultStatus.CONCERN),
        _check_result(sql.CheckResultStatus.SUGGESTION),
        _check_result(sql.CheckResultStatus.NOTE),
    ]
    members = collections.Counter({sql.CheckResultStatus.CONCERN: 2})
    assert report._reconciliation(primary, members) == "3 concerns: 1 on this file, 2 on files inside this archive."


def _check_result(status: sql.CheckResultStatus) -> sql.CheckResult:
    return sql.CheckResult(
        id=0,
        release_key="test-0.1",
        revision_number="00001",
        checker="atr.tasks.checks.rat.check_licenses",
        checker_version="4",
        primary_rel_path="source.zip",
        member_rel_path=None,
        created=datetime.datetime.now(datetime.UTC),
        status=status,
        message="test",
        data=None,
        inputs_hash=None,
    )
