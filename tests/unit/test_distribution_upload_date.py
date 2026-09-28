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
import sqlalchemy

import atr.models.basic as basic
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared.distribution as distribution


@pytest.mark.parametrize(
    ("platform", "data"),
    [
        (sql.DistributionPlatform.NPM, {"time": {"18.2.0": "2022-06-14T19:46:38.369Z"}}),
        (sql.DistributionPlatform.NPM_SCOPED, {"time": {"18.2.0": "2022-06-14T19:46:38.369Z"}}),
        (sql.DistributionPlatform.PYPI, {"urls": [{"upload_time_iso_8601": "2022-06-14T19:46:38.369Z"}]}),
        (sql.DistributionPlatform.DOCKER_HUB, {"tag_last_pushed": "2022-06-14T19:46:38.369Z"}),
        (sql.DistributionPlatform.NPM, {"time": {"18.2.0": "2022-06-14T21:46:38.369+02:00"}}),
    ],
    ids=["npm", "npm-scoped", "pypi", "docker-hub", "explicit-offset"],
)
def test_distribution_upload_date_round_trip(platform: sql.DistributionPlatform, data: basic.JSON) -> None:
    upload_date = distribution.distribution_upload_date(platform, data, safe.VersionKey("18.2.0"))
    expected = datetime.datetime(2022, 6, 14, 19, 46, 38, 369000, tzinfo=datetime.UTC)
    assert upload_date == expected

    table = sqlalchemy.Table(
        "upload_dates",
        sqlalchemy.MetaData(),
        sqlalchemy.Column("upload_date", sql.Distribution.__table__.c.upload_date.type),
    )
    engine = sqlalchemy.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            table.create(connection)
            connection.execute(table.insert().values(upload_date=upload_date))
            stored = connection.execute(sqlalchemy.select(table.c.upload_date)).scalar_one()
        assert stored == expected
        assert stored.utcoffset() == datetime.timedelta(0)
    finally:
        engine.dispose()
