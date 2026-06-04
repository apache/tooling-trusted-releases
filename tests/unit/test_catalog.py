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

import atr.get.catalog as catalog
import atr.models.sql as sql

_NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


def _project(version_method: sql.VersionMethod = sql.VersionMethod.SIMPLE) -> sql.Project:
    committee = sql.Committee(key="cassandra", name="Cassandra")
    return sql.Project(key="cassandra", name="Apache Cassandra", committee=committee, version_method=version_method)


def _release(
    project: sql.Project,
    version: str,
    *,
    cycle_key: str,
    released: datetime.datetime | None = None,
    archived: datetime.datetime | None = None,
) -> sql.Release:
    return sql.Release(
        phase=sql.ReleasePhase.RELEASE,
        version=version,
        project=project,
        project_key=project.key,
        cycle_key=cycle_key,
        created=datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC),
        released=released,
        archived=archived,
    )


def _artifact(
    project: sql.Project,
    version: str,
    path: str,
    *,
    release: sql.Release | None = None,
    svn_revision: int | None = None,
) -> sql.Artifact:
    return sql.Artifact(
        project_key=project.key,
        version=version,
        artifact_path=path,
        release_key=(release.key if release is not None else None),
        release=release,
        svn_revision=svn_revision,
    )


class _CatalogVersionStub:
    def __init__(self, version: str, *, cycle: sql.ProjectCycle | None) -> None:
        self.value = catalog._CatalogVersion(
            version=version,
            status="released" if cycle is not None else "archived",
            released=None,
            svn_revision=None,
            cycle=cycle,
            artifacts=[],
        )


def _cycle(
    cycle: str,
    *,
    latest: datetime.datetime | None = None,
    lts: bool = False,
    eol: datetime.datetime | None = None,
) -> sql.ProjectCycle:
    return sql.ProjectCycle(
        cycle_key=f"cassandra-{cycle}", cycle=cycle, project_key="cassandra", latest=latest, lts=lts, eol=eol
    )


def test_simple_projects_use_the_flat_layout() -> None:
    assert catalog._grouped_layout(sql.VersionMethod.SIMPLE) is False


def test_versioned_projects_use_the_grouped_layout() -> None:
    assert catalog._grouped_layout(sql.VersionMethod.SEMVER) is True
    assert catalog._grouped_layout(sql.VersionMethod.CALVER) is True


def test_status_reflects_release_and_archive_state() -> None:
    project = _project()
    released = _release(project, "5.0.2", cycle_key="cassandra-default", released=_NOW)
    archived = _release(project, "4.1.7", cycle_key="cassandra-default", released=_NOW, archived=_NOW)
    artifacts = [
        _artifact(project, "5.0.2", "a-5.0.2.tar.gz", release=released),
        _artifact(project, "4.1.7", "a-4.1.7.tar.gz", release=archived),
    ]

    versions = {v.version: v for v in catalog._catalog_versions(artifacts, {}, project.committee_key)}

    assert versions["5.0.2"].status == "released"
    assert versions["4.1.7"].status == "archived"


def test_only_released_versions_are_downloadable() -> None:
    project = _project()
    released = _release(project, "5.0.2", cycle_key="cassandra-default", released=_NOW)
    archived = _release(project, "4.1.7", cycle_key="cassandra-default", released=_NOW, archived=_NOW)
    artifacts = [
        _artifact(project, "5.0.2", "a-5.0.2.tar.gz", release=released),
        _artifact(project, "4.1.7", "a-4.1.7.tar.gz", release=archived, svn_revision=28114),
    ]

    versions = {v.version: v for v in catalog._catalog_versions(artifacts, {}, project.committee_key)}

    assert versions["5.0.2"].artifacts[0].downloadable is True
    assert versions["4.1.7"].artifacts[0].downloadable is False
    assert versions["4.1.7"].svn_revision == 28114


def test_versions_are_newest_first() -> None:
    project = _project()
    older = _release(
        project, "5.0.1", cycle_key="cassandra-default", released=datetime.datetime(2025, 8, 2, tzinfo=datetime.UTC)
    )
    newer = _release(
        project, "5.0.2", cycle_key="cassandra-default", released=datetime.datetime(2025, 10, 14, tzinfo=datetime.UTC)
    )
    artifacts = [
        _artifact(project, "5.0.1", "a-5.0.1.tar.gz", release=older),
        _artifact(project, "5.0.2", "a-5.0.2.tar.gz", release=newer),
    ]

    versions = catalog._catalog_versions(artifacts, {}, project.committee_key)

    assert [v.version for v in versions] == ["5.0.2", "5.0.1"]


def test_cycles_are_ordered_by_latest_activity() -> None:
    five = _cycle("5.0", latest=datetime.datetime(2025, 10, 1, tzinfo=datetime.UTC))
    four = _cycle("4.1", latest=datetime.datetime(2025, 9, 1, tzinfo=datetime.UTC))
    versions = [
        _CatalogVersionStub("4.1.7", cycle=four).value,
        _CatalogVersionStub("5.0.2", cycle=five).value,
    ]

    cycles = catalog._catalog_cycles(versions, _NOW)

    assert [c.cycle.cycle for c in cycles if c.cycle is not None] == ["5.0", "4.1"]


def test_versions_without_a_cycle_are_excluded_from_the_grouped_layout() -> None:
    default = _cycle("default", latest=datetime.datetime(2025, 5, 1, tzinfo=datetime.UTC))
    versions = [
        _CatalogVersionStub("2.0.0", cycle=default).value,
        _CatalogVersionStub("1.0.0", cycle=None).value,
    ]

    cycles = catalog._catalog_cycles(versions, _NOW)

    assert [c.cycle.cycle for c in cycles if c.cycle is not None] == ["default"]


def test_lifecycle_badge_prefers_lts_then_eol_then_active() -> None:
    past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    assert catalog._lifecycle_badge(_cycle("5.0", lts=True, eol=past), _NOW) == "LTS"
    assert catalog._lifecycle_badge(_cycle("4.0", eol=past), _NOW) == "EOL"
    assert catalog._lifecycle_badge(_cycle("5.0"), _NOW) == "Active"
