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

import pytest
import sqlmodel

import atr.models.sql as sql


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (sql.BallotPaper, ("created",)),
        (sql.CheckResultIgnore, ("created",)),
        (sql.Quarantined, ("created", "completed")),
        (sql.Release, ("activity_at", "archived", "created", "released", "vote_resolved", "vote_started")),
        (sql.Revision, ("created",)),
        (sql.SigningCertificate, ("latest_self_signature",)),
        (sql.SigningKey, ("created", "expires")),
        (sql.Task, ("added", "started", "completed")),
    ],
)
@pytest.mark.parametrize("timestamp", ["2026-09-28T12:34:56.123456Z", "2026-09-28T14:34:56.123456+02:00"])
def test_model_datetime_strings_preserve_timezone(
    model: type[sqlmodel.SQLModel], fields: tuple[str, ...], timestamp: str
) -> None:
    record = model(**dict.fromkeys(fields, timestamp))
    expected = datetime.datetime(2026, 9, 28, 12, 34, 56, 123456, tzinfo=datetime.UTC)
    for field in fields:
        assert getattr(record, field) == expected
