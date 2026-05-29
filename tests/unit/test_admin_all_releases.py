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
from types import SimpleNamespace

import atr.admin as admin


def test_release_age_row_bold_at_91_days() -> None:
    now = datetime.datetime(2025, 7, 1, tzinfo=datetime.UTC)
    created = now - datetime.timedelta(days=91)
    release = _make_release(created=created, days_since_active=91)
    row = admin._release_age_row(release, now)
    assert row.age_bold is True
    assert row.inactive_bold is True


def test_release_age_row_bold_just_over_90_days() -> None:
    now = datetime.datetime(2025, 7, 1, tzinfo=datetime.UTC)
    created = now - datetime.timedelta(days=90, seconds=1)
    release = _make_release(created=created, days_since_active=0)
    row = admin._release_age_row(release, now)
    assert row.age_bold is True
    assert row.age_label == "90 days"


def test_release_age_row_inactive_label() -> None:
    now = datetime.datetime(2025, 7, 1, tzinfo=datetime.UTC)
    release = _make_release(created=now, days_since_active=5)
    row = admin._release_age_row(release, now)
    assert row.inactive_label == "5 days"


def test_release_age_row_inactive_not_bold_despite_old_age() -> None:
    now = datetime.datetime(2025, 7, 1, tzinfo=datetime.UTC)
    created = now - datetime.timedelta(days=120)
    release = _make_release(created=created, days_since_active=10)
    row = admin._release_age_row(release, now)
    assert row.age_bold is True
    assert row.inactive_bold is False


def test_release_age_row_not_bold_at_90_days() -> None:
    now = datetime.datetime(2025, 7, 1, tzinfo=datetime.UTC)
    created = now - datetime.timedelta(days=90)
    release = _make_release(created=created, days_since_active=90)
    row = admin._release_age_row(release, now)
    assert row.age_bold is False
    assert row.inactive_bold is False


def _make_release(created: datetime.datetime, days_since_active: int) -> SimpleNamespace:
    return SimpleNamespace(created=created, days_since_active=days_since_active)
