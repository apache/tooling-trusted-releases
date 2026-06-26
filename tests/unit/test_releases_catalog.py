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

import atr.get.release as release
import atr.models.sql as sql


def test_committees_sorted_by_display_name() -> None:
    solr = sql.Committee(key="solr", name="Solr")
    commons = sql.Committee(key="commons", name="Commons")
    solr_project = sql.Project(key="solr", name="Apache Solr", committee=solr)
    commons_project = sql.Project(key="commons-io", name="Apache Commons IO", committee=commons)
    releases = [
        _release(solr_project, "9.6.1", datetime.datetime(2025, 4, 1, tzinfo=datetime.UTC)),
        _release(commons_project, "2.16.1", datetime.datetime(2025, 4, 1, tzinfo=datetime.UTC)),
    ]

    catalog = release._committee_release_catalog(releases)

    assert [entry.committee.display_name for entry in catalog] == ["Commons", "Solr"]


def test_counts_and_latest_version_by_date() -> None:
    tika = sql.Committee(key="tika", name="Tika")
    project = sql.Project(key="tika", name="Apache Tika", committee=tika)
    releases = [
        _release(project, "2.9.1", datetime.datetime(2025, 1, 10, tzinfo=datetime.UTC)),
        _release(project, "2.9.2", datetime.datetime(2025, 5, 10, tzinfo=datetime.UTC)),
        _release(project, "2.9.0", datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)),
    ]

    catalog = release._committee_release_catalog(releases)

    entry = catalog[0].projects[0]
    assert entry.finished_count == 3
    assert entry.latest_version == "2.9.2"


def test_empty_version_yields_no_latest() -> None:
    tika = sql.Committee(key="tika", name="Tika")
    project = sql.Project(key="tika", name="Apache Tika", committee=tika)
    releases = [_release(project, "", datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC))]

    catalog = release._committee_release_catalog(releases)

    assert catalog[0].projects[0].latest_version is None


def test_groups_projects_under_their_committee() -> None:
    commons = sql.Committee(key="commons", name="Commons")
    io = sql.Project(key="commons-io", name="Apache Commons IO", committee=commons)
    lang = sql.Project(key="commons-lang", name="Apache Commons Lang", committee=commons)
    releases = [
        _release(io, "2.16.1", datetime.datetime(2025, 3, 1, tzinfo=datetime.UTC)),
        _release(lang, "3.14.0", datetime.datetime(2025, 2, 1, tzinfo=datetime.UTC)),
    ]

    catalog = release._committee_release_catalog(releases)

    assert len(catalog) == 1
    assert catalog[0].committee.key == "commons"
    assert [entry.project.display_name for entry in catalog[0].projects] == [
        "Apache Commons IO",
        "Apache Commons Lang",
    ]


def test_release_with_no_committee_is_skipped() -> None:
    orphan = sql.Project(key="orphan", name="Apache Orphan", committee=None)
    releases = [_release(orphan, "1.0.0", datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC))]

    catalog = release._committee_release_catalog(releases)

    assert catalog == []


def _release(project: sql.Project, version: str, released: datetime.datetime | None) -> sql.Release:
    return sql.Release(
        phase=sql.ReleasePhase.RELEASE,
        version=version,
        project=project,
        project_key=project.key,
        created=datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC),
        released=released,
    )
