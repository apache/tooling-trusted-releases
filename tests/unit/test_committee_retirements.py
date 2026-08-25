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

import pytest

import atr.datasources.apache as apache
import atr.models.sql as sql

RETIRED_COMMITTEES = apache.RetiredCommitteeData.model_validate(
    {
        "last_updated": "2026-08-25 12:00:00 UTC",
        "retired_count": 1,
        "retired": {
            "abdera": {
                "display_name": "Abdera",
                "description": "Atom Publishing Protocol Implementation",
                "retired": "2017-02",
                "retired_date": "2017-02-27",
            }
        },
    }
)

WHIMSY_PODLINGS = apache.WhimsyPodlingsData.model_validate(
    {
        "last_updated": "2026-08-25 12:00:00 UTC",
        "podling_count": 2,
        "status_counts": {"current": 1, "retired": 1},
        "podling": {
            "amoro": {"name": "Amoro", "status": "current"},
            "hivemall": {"name": "Hivemall", "status": "retired", "enddate": "2022-09-01"},
        },
    }
)


def _data_with(committees: list[sql.Committee]) -> mock.MagicMock:
    query = mock.MagicMock()
    query.all = mock.AsyncMock(return_value=committees)
    data = mock.MagicMock()
    data.committee.return_value = query
    return data


@pytest.mark.asyncio
async def test_update_retirements_archives_and_restores() -> None:
    abdera = sql.Committee(key="abdera")
    hivemall = sql.Committee(key="hivemall", is_podling=True)
    sourcelume = sql.Committee(key="sourcelume", is_archived=True)
    java = sql.Committee(key="java")
    data = _data_with([abdera, hivemall, sourcelume, java])

    updated = await apache._update_retirements(data, {"sourcelume"}, RETIRED_COMMITTEES, WHIMSY_PODLINGS)

    assert updated == 3
    assert abdera.is_archived is True
    assert abdera.archived == datetime.datetime(2017, 2, 27, tzinfo=datetime.UTC)
    assert hivemall.is_archived is True
    assert hivemall.archived == datetime.datetime(2022, 9, 1, tzinfo=datetime.UTC)
    assert sourcelume.is_archived is False
    assert sourcelume.archived is None
    assert java.is_archived is False


@pytest.mark.asyncio
async def test_update_retirements_is_idempotent() -> None:
    abdera = sql.Committee(key="abdera")
    data = _data_with([abdera])

    first = await apache._update_retirements(data, set(), RETIRED_COMMITTEES, WHIMSY_PODLINGS)
    second = await apache._update_retirements(data, set(), RETIRED_COMMITTEES, WHIMSY_PODLINGS)

    assert first == 1
    assert second == 0
