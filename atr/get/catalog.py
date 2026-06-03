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
import dataclasses
import datetime
from collections.abc import Sequence
from typing import Final, Literal

import asfquart.base as base

import atr.api as api
import atr.blueprints.get as get
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.template as template
import atr.util as util
import atr.web as web

_GROUPED_METHODS: Final[frozenset[sql.VersionMethod]] = frozenset({sql.VersionMethod.SEMVER, sql.VersionMethod.CALVER})


@get.typed
async def project(_session: web.Public, _catalog: Literal["catalog"], project_key: safe.ProjectKey) -> str:
    """
    URL: /catalog/<project_key>
    The release catalogue for a project: versions and their artifacts.
    """
    async with db.session() as data:
        project_obj = await data.project(key=str(project_key), status=sql.ProjectStatus.ACTIVE).demand(
            base.ASFQuartException(f"Project {project_key} not found", errorcode=404)
        )
        artifacts = await data.artifact(project_key=project_obj.key, _release=True).all()
        project_cycles = await data.project_cycle(project_key=project_obj.key).all()

    cycles_by_key = {cycle.cycle_key: cycle for cycle in project_cycles}
    now = datetime.datetime.now(datetime.UTC)
    versions = _catalog_versions(artifacts, cycles_by_key)
    grouped = _grouped_layout(project_obj.version_method)
    cycles = _catalog_cycles(versions, now) if grouped else []

    return await template.render(
        "catalog.html",
        project=project_obj,
        versions=versions,
        cycles=cycles,
        grouped=grouped,
        api=api,
        format_datetime=util.format_datetime,
    )


@dataclasses.dataclass
class _CatalogArtifact:
    artifact_path: str
    classification: str | None
    signature_path: str | None
    checksum_path: str | None
    key_fingerprint: str | None
    svn_revision: int | None
    downloadable: bool


@dataclasses.dataclass
class _CatalogVersion:
    version: str
    status: Literal["released", "archived"]
    released: datetime.datetime | None
    svn_revision: int | None
    cycle: sql.ProjectCycle | None
    artifacts: list[_CatalogArtifact]


@dataclasses.dataclass
class _CatalogCycle:
    cycle: sql.ProjectCycle | None
    lifecycle: str | None
    versions: list[_CatalogVersion]


def _grouped_layout(version_method: sql.VersionMethod) -> bool:
    # Simple projects have only the default cycle, so they skip cycle grouping.
    return version_method in _GROUPED_METHODS


def _catalog_versions(
    artifacts: Sequence[sql.Artifact],
    cycles_by_key: dict[str, sql.ProjectCycle],
) -> list[_CatalogVersion]:
    by_version: dict[str, list[sql.Artifact]] = collections.defaultdict(list)
    for artifact in artifacts:
        by_version[artifact.version].append(artifact)

    versions: list[_CatalogVersion] = []
    for version, rows in by_version.items():
        release = next((row.release for row in rows if row.release is not None), None)
        status = _status(release)
        # Archived versions aren't served from the download route, so show metadata only.
        downloadable = status == "released"
        cycle = cycles_by_key.get(release.cycle_key) if (release is not None) else None
        svn_revisions = [row.svn_revision for row in rows if row.svn_revision is not None]
        versions.append(
            _CatalogVersion(
                version=version,
                status=status,
                released=(release.released or release.created) if (release is not None) else None,
                svn_revision=max(svn_revisions) if svn_revisions else None,
                cycle=cycle,
                artifacts=[
                    _CatalogArtifact(
                        artifact_path=row.artifact_path,
                        classification=row.classification,
                        signature_path=row.signature_path,
                        checksum_path=row.checksum_path,
                        key_fingerprint=row.key_fingerprint,
                        svn_revision=row.svn_revision,
                        downloadable=downloadable,
                    )
                    for row in rows
                ],
            )
        )

    versions.sort(key=_version_sort_key, reverse=True)
    return versions


def _catalog_cycles(versions: list[_CatalogVersion], now: datetime.datetime) -> list[_CatalogCycle]:
    by_cycle: dict[str, list[_CatalogVersion]] = collections.defaultdict(list)
    cycles: dict[str, sql.ProjectCycle] = {}
    for version in versions:
        if version.cycle is None:
            continue
        by_cycle[version.cycle.cycle_key].append(version)
        cycles[version.cycle.cycle_key] = version.cycle

    catalog = [
        _CatalogCycle(cycle=cycles[key], lifecycle=_lifecycle_badge(cycles[key], now), versions=grouped)
        for key, grouped in by_cycle.items()
    ]
    catalog.sort(key=_cycle_sort_key, reverse=True)
    return catalog


def _lifecycle_badge(cycle: sql.ProjectCycle, now: datetime.datetime) -> str:
    if cycle.lts:
        return "LTS"
    if (cycle.eol is not None) and (cycle.eol <= now):
        return "EOL"
    return "Active"


def _cycle_sort_key(cycle: _CatalogCycle) -> datetime.datetime:
    # Most recently active cycle first.
    latest = cycle.cycle.latest if (cycle.cycle is not None) else None
    return latest or datetime.datetime.min.replace(tzinfo=datetime.UTC)


def _status(release: sql.Release | None) -> Literal["released", "archived"]:
    # Treat a missing release as archived, so it is never shown as downloadable.
    if (release is not None) and (release.archived is None):
        return "released"
    return "archived"


def _version_sort_key(version: _CatalogVersion) -> tuple[datetime.datetime, int]:
    # Newest first: released date leads, svn revision breaks ties for older rows.
    released = version.released or datetime.datetime.min.replace(tzinfo=datetime.UTC)
    return (released, version.svn_revision or 0)
