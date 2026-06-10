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

import atr.models.api as api
import atr.models.sql as sql
import atr.shared.catalog as catalog

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
    sbom_path: str | None = None,
) -> sql.Artifact:
    return sql.Artifact(
        project_key=project.key,
        version=version,
        artifact_path=path,
        release_key=(release.key if release is not None else None),
        release=release,
        svn_revision=svn_revision,
        sbom_path=sbom_path,
    )


class _CatalogVersionStub:
    def __init__(self, version: str, *, cycle: sql.ProjectCycle | None) -> None:
        self.value = api.CatalogVersion(
            version=version,
            status="released" if cycle is not None else "archived",
            released=None,
            svn_revision=None,
            cycle=cycle.cycle if cycle is not None else None,
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

    versions = {str(v.version): v for v in catalog._versions(artifacts, {}, project.committee_key)}

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

    versions = {str(v.version): v for v in catalog._versions(artifacts, {}, project.committee_key)}

    assert versions["5.0.2"].artifacts[0].downloadable is True
    assert versions["4.1.7"].artifacts[0].downloadable is False
    assert versions["4.1.7"].svn_revision == 28114


def test_paired_sbom_shown_on_catalog_artifact() -> None:
    project = _project()
    released = _release(project, "5.0.2", cycle_key="cassandra-default", released=_NOW)
    artifacts = [
        _artifact(project, "5.0.2", "a-5.0.2.tar.gz", release=released, sbom_path="a-5.0.2.tar.gz.cdx.json"),
    ]

    versions = {str(v.version): v for v in catalog._versions(artifacts, {}, project.committee_key)}

    assert versions["5.0.2"].artifacts[0].sbom_path == "a-5.0.2.tar.gz.cdx.json"


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

    versions = catalog._versions(artifacts, {}, project.committee_key)

    assert [str(v.version) for v in versions] == ["5.0.2", "5.0.1"]


def test_cycles_are_ordered_by_latest_activity() -> None:
    five = _cycle("5.0", latest=datetime.datetime(2025, 10, 1, tzinfo=datetime.UTC))
    four = _cycle("4.1", latest=datetime.datetime(2025, 9, 1, tzinfo=datetime.UTC))
    versions = [
        _CatalogVersionStub("4.1.7", cycle=four).value,
        _CatalogVersionStub("5.0.2", cycle=five).value,
    ]
    cycles_by_label = {c.cycle: c for c in (five, four)}

    cycles = catalog._cycles(versions, cycles_by_label, _NOW)

    assert [c.cycle for c in cycles] == ["5.0", "4.1"]


def test_versions_without_a_cycle_are_excluded_from_grouping() -> None:
    default = _cycle("default", latest=datetime.datetime(2025, 5, 1, tzinfo=datetime.UTC))
    versions = [
        _CatalogVersionStub("2.0.0", cycle=default).value,
        _CatalogVersionStub("1.0.0", cycle=None).value,
    ]
    cycles_by_label = {default.cycle: default}

    cycles = catalog._cycles(versions, cycles_by_label, _NOW)

    assert [c.cycle for c in cycles] == ["default"]


def test_lifecycle_badge_prefers_lts_then_eol_then_active() -> None:
    past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    assert catalog._lifecycle_badge(_cycle("5.0", lts=True, eol=past), _NOW) == "LTS"
    assert catalog._lifecycle_badge(_cycle("4.0", eol=past), _NOW) == "EOL"
    assert catalog._lifecycle_badge(_cycle("5.0"), _NOW) == "Active"


def test_cle_url_links_versions_backed_by_a_release_when_a_host_is_supplied() -> None:
    project = _project()
    released = _release(project, "5.0.2", cycle_key="cassandra-default", released=_NOW)
    artifacts = [_artifact(project, "5.0.2", "a-5.0.2.tar.gz", release=released)]

    versions = {str(v.version): v for v in catalog._versions(artifacts, {}, project.committee_key, "atr.example.org")}

    assert versions["5.0.2"].cle_url == "https://atr.example.org/api/cle/release/cassandra/5.0.2"


def test_cle_url_is_omitted_for_versions_without_a_backing_release() -> None:
    project = _project()
    artifacts = [_artifact(project, "3.0.0", "a-3.0.0.tar.gz", svn_revision=100)]

    versions = {str(v.version): v for v in catalog._versions(artifacts, {}, project.committee_key, "atr.example.org")}

    assert versions["3.0.0"].cle_url is None


def test_cle_url_is_omitted_when_no_host_is_supplied() -> None:
    # The page opts out of absolute URLs, building its own relative CLE links instead.
    project = _project()
    released = _release(project, "5.0.2", cycle_key="cassandra-default", released=_NOW)
    artifacts = [_artifact(project, "5.0.2", "a-5.0.2.tar.gz", release=released)]

    versions = {str(v.version): v for v in catalog._versions(artifacts, {}, project.committee_key)}

    assert versions["5.0.2"].cle_url is None
