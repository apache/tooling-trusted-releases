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


def test_last_activity_at_created_only() -> None:
    created = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
    release = _make_release(created=created)
    assert admin._last_activity_at(release) == created


def test_last_activity_at_released_newest() -> None:
    created = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
    released = datetime.datetime(2025, 4, 1, tzinfo=datetime.UTC)
    release = _make_release(
        created=created,
        vote_started=datetime.datetime(2025, 2, 1, tzinfo=datetime.UTC),
        vote_resolved=datetime.datetime(2025, 3, 1, tzinfo=datetime.UTC),
        released=released,
    )
    assert admin._last_activity_at(release) == released


def test_last_activity_at_released_no_revisions() -> None:
    created = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
    released = datetime.datetime(2025, 4, 1, tzinfo=datetime.UTC)
    release = _make_release(created=created, released=released)
    assert admin._last_activity_at(release) == released


def test_last_activity_at_revision_newest() -> None:
    created = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
    rev1 = _make_revision(datetime.datetime(2025, 1, 5, tzinfo=datetime.UTC))
    rev2 = _make_revision(datetime.datetime(2025, 2, 10, tzinfo=datetime.UTC))
    release = _make_release(created=created, revisions=[rev1, rev2])
    assert admin._last_activity_at(release) == rev2.created


def test_last_activity_at_vote_started_newest() -> None:
    created = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
    rev = _make_revision(datetime.datetime(2025, 1, 5, tzinfo=datetime.UTC))
    vote_started = datetime.datetime(2025, 2, 1, tzinfo=datetime.UTC)
    release = _make_release(created=created, revisions=[rev], vote_started=vote_started)
    assert admin._last_activity_at(release) == vote_started


def test_release_age_row_bold_at_91_days() -> None:
    now = datetime.datetime(2025, 7, 1, tzinfo=datetime.UTC)
    created = now - datetime.timedelta(days=91)
    release = _make_release(created=created)
    row = admin._release_age_row(release, now)
    assert row.age_bold is True
    assert row.inactive_bold is True


def test_release_age_row_bold_just_over_90_days() -> None:
    now = datetime.datetime(2025, 7, 1, tzinfo=datetime.UTC)
    created = now - datetime.timedelta(days=90, seconds=1)
    release = _make_release(created=created)
    row = admin._release_age_row(release, now)
    assert row.age_bold is True
    assert row.age_label == "90 days"


def test_release_age_row_inactive_not_bold_despite_old_age() -> None:
    now = datetime.datetime(2025, 7, 1, tzinfo=datetime.UTC)
    created = now - datetime.timedelta(days=120)
    rev = _make_revision(now - datetime.timedelta(days=10))
    release = _make_release(created=created, revisions=[rev])
    row = admin._release_age_row(release, now)
    assert row.age_bold is True
    assert row.inactive_bold is False


def test_release_age_row_not_bold_at_90_days() -> None:
    now = datetime.datetime(2025, 7, 1, tzinfo=datetime.UTC)
    created = now - datetime.timedelta(days=90)
    release = _make_release(created=created)
    row = admin._release_age_row(release, now)
    assert row.age_bold is False
    assert row.inactive_bold is False


def _make_release(
    created: datetime.datetime,
    revisions: list[SimpleNamespace] | None = None,
    vote_started: datetime.datetime | None = None,
    vote_resolved: datetime.datetime | None = None,
    released: datetime.datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        created=created,
        revisions=revisions or [],
        vote_started=vote_started,
        vote_resolved=vote_resolved,
        released=released,
    )


def _make_revision(created: datetime.datetime) -> SimpleNamespace:
    return SimpleNamespace(created=created)
