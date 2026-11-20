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
from typing import Final

import atr.models.sql as sql
import atr.tasks.distribution as distribution

_NOW: Final = datetime.datetime(2026, 11, 19, 12, 0, tzinfo=datetime.UTC)


def test_due_leaves_out_recently_checked() -> None:
    recent = _checking("recent", retries=3, checked_minutes_ago=1)
    overdue = _checking("overdue", retries=3, checked_minutes_ago=3)
    assert distribution._due([recent, overdue], _NOW) == [overdue]


def test_due_longest_overdue_first() -> None:
    late = _checking("late", retries=20, checked_minutes_ago=61)
    later = _checking("later", retries=20, checked_minutes_ago=90)
    assert distribution._due([late, later], _NOW) == [later, late]


def test_due_staging_waits_like_any_other() -> None:
    staged = _checking("staged", retries=4, checked_minutes_ago=0, staging=True)
    assert distribution._due([staged], _NOW) == []


def test_due_unchecked_always() -> None:
    unchecked = sql.Distribution(
        release_key="example-1.0",
        platform=sql.DistributionPlatform.MAVEN,
        owner_namespace="org.apache.example",
        package="unchecked",
        version="1.0",
        status=sql.DistributionStatus.CHECKING,
    )
    assert distribution._due([unchecked], _NOW) == [unchecked]


def test_next_check_slows_after_fast_checks() -> None:
    fast = distribution._next_check(distribution._FAST_CHECKS - 1, _NOW)
    slow = distribution._next_check(distribution._FAST_CHECKS, _NOW)
    assert fast - _NOW == distribution._CHECK_INTERVAL_MIN
    assert slow - _NOW == distribution._CHECK_INTERVAL_MAX


def _checking(package: str, *, retries: int, checked_minutes_ago: int, staging: bool = False) -> sql.Distribution:
    return sql.Distribution(
        release_key="example-1.0",
        platform=sql.DistributionPlatform.MAVEN,
        owner_namespace="org.apache.example",
        package=package,
        version="1.0",
        staging=staging,
        status=sql.DistributionStatus.CHECKING,
        retries=retries,
        last_checked=_NOW - datetime.timedelta(minutes=checked_minutes_ago),
    )
