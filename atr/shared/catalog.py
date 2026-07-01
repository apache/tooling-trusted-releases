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

import atr.models as models
import atr.models.safe as safe
import atr.models.sql as sql
import atr.util as util

_GROUPED_METHODS: Final[frozenset[sql.VersionMethod]] = frozenset({sql.VersionMethod.SEMVER, sql.VersionMethod.CALVER})


@dataclasses.dataclass
class CatalogProject:
    versions: list[models.api.CatalogVersion]
    cycles: list[models.api.CatalogCycle]
    grouped: bool


def cle_project_url(atr_host: str, project_key: str) -> str:
    return f"https://{atr_host}/api/cle/project/{project_key}"


def cle_release_url(atr_host: str, project_key: str, version: str) -> str:
    return f"https://{atr_host}/api/cle/release/{project_key}/{version}"


def assemble(
    version_method: sql.VersionMethod,
    artifacts: Sequence[sql.Artifact],
    project_cycles: Sequence[sql.ProjectCycle],
    now: datetime.datetime,
    atr_host: str | None = None,
) -> CatalogProject:
    # `atr_host` is only supplied by the API, which needs absolute CLE links. The
    # page leaves it None and renders its own relative links via as_url instead.
    cycles_by_key = {cycle.cycle_key: cycle for cycle in project_cycles}
    versions = _versions(artifacts, cycles_by_key, atr_host)
    grouped = _grouped_layout(version_method)
    if grouped:
        # Cycle labels are unique within a project, so they group and look up safely.
        cycles_by_label = {cycle.cycle: cycle for cycle in project_cycles}
        cycles = _cycles(versions, cycles_by_label, now)
    else:
        cycles = []
    return CatalogProject(versions=versions, cycles=cycles, grouped=grouped)


def _artifact(row: sql.Artifact, downloadable: bool, archived: bool) -> models.api.CatalogArtifact:
    artifact_url: str | None = None
    signature_url: str | None = None
    checksum_url: str | None = None
    sbom_url: str | None = None
    if downloadable and row.download_path_suffix:
        # The stored directory is the file's real dist location, so a moved project's artifacts
        # (graduated from an umbrella, or shifted to the Attic) resolve where they actually live. The
        # signature, checksum and SBOM share the directory
        directory = safe.RelPath(row.download_path_suffix)
        artifact_url = util.download_url_for_path(
            directory.append(row.artifact_path), util.DownloadFile.ARTIFACT, archived=archived
        )
        if row.signature_path:
            signature_url = util.download_url_for_path(
                directory.append(row.signature_path), util.DownloadFile.METADATA, archived=archived
            )
        if row.checksum_path:
            checksum_url = util.download_url_for_path(
                directory.append(row.checksum_path), util.DownloadFile.METADATA, archived=archived
            )
        if row.sbom_path:
            sbom_url = util.download_url_for_path(
                directory.append(row.sbom_path), util.DownloadFile.METADATA, archived=archived
            )
    return models.api.CatalogArtifact(
        artifact_path=row.artifact_path,
        classification=row.classification,
        signature_path=row.signature_path,
        checksum_path=row.checksum_path,
        sbom_path=row.sbom_path,
        key_fingerprint=row.key_fingerprint,
        svn_revision=row.svn_revision,
        managed=row.managed,
        dated=row.dated,
        downloadable=downloadable,
        artifact_url=artifact_url,
        signature_url=signature_url,
        checksum_url=checksum_url,
        sbom_url=sbom_url,
    )


def _grouped_layout(version_method: sql.VersionMethod) -> bool:
    # Simple projects have only the default cycle, so they skip cycle grouping.
    return version_method in _GROUPED_METHODS


def _cycles(
    versions: Sequence[models.api.CatalogVersion],
    cycles_by_label: dict[str, sql.ProjectCycle],
    now: datetime.datetime,
) -> list[models.api.CatalogCycle]:
    by_label: dict[str, list[models.api.CatalogVersion]] = collections.defaultdict(list)
    for version in versions:
        if version.cycle is None:
            continue
        by_label[version.cycle].append(version)

    catalog = [
        models.api.CatalogCycle(cycle=label, lifecycle=_lifecycle_badge(cycles_by_label[label], now), versions=grouped)
        for label, grouped in by_label.items()
    ]
    catalog.sort(key=lambda c: _cycle_sort_key(cycles_by_label[c.cycle]), reverse=True)
    return catalog


def _cycle_sort_key(cycle: sql.ProjectCycle) -> datetime.datetime:
    # Most recently active cycle first.
    return cycle.latest or datetime.datetime.min.replace(tzinfo=datetime.UTC)


def _lifecycle_badge(cycle: sql.ProjectCycle, now: datetime.datetime) -> str:
    if cycle.lts:
        return "LTS"
    if (cycle.eol is not None) and (cycle.eol <= now):
        return "EOL"
    return "Active"


def _status(release: sql.Release | None) -> Literal["released", "archived"]:
    # Treat a missing release as archived, so it is never shown as downloadable.
    if (release is not None) and not release.is_archived:
        return "released"
    return "archived"


def _version_sort_key(version: models.api.CatalogVersion) -> tuple[datetime.datetime, int]:
    # Newest first: released date leads, svn revision breaks ties for older rows.
    released = version.released or datetime.datetime.min.replace(tzinfo=datetime.UTC)
    return (released, version.svn_revision or 0)


def _versions(
    artifacts: Sequence[sql.Artifact],
    cycles_by_key: dict[str, sql.ProjectCycle],
    atr_host: str | None = None,
) -> list[models.api.CatalogVersion]:
    by_version: dict[safe.VersionKey, list[sql.Artifact]] = collections.defaultdict(list)
    for artifact in artifacts:
        by_version[artifact.safe_version_key].append(artifact)

    versions: list[models.api.CatalogVersion] = []
    for version, rows in by_version.items():
        release = next((row.release for row in rows if row.release is not None), None)
        status = _status(release)
        # Released files come off the live download route, archived ones off archive.apache.org;
        # either way they're downloadable
        downloadable = status in ("released", "archived")
        cycle = cycles_by_key.get(release.cycle_key) if (release is not None) else None
        svn_revisions = [row.svn_revision for row in rows if row.svn_revision is not None]
        # CLE only resolves for a released-phase record, so historical svn-only
        # versions (no release) get no link rather than one that would 404.
        cle_eligible = (release is not None) and (release.phase == sql.ReleasePhase.RELEASE)
        versions.append(
            models.api.CatalogVersion(
                version=version,
                status=status,
                released=(release.released or release.created) if (release is not None) else None,
                svn_revision=max(svn_revisions) if svn_revisions else None,
                managed=any(row.managed for row in rows),
                cycle=cycle.cycle if (cycle is not None) else None,
                cle_url=cle_release_url(atr_host, release.project_key, str(version))
                if (atr_host is not None) and cle_eligible and (release is not None)
                else None,
                artifacts=[_artifact(row, downloadable, status == "archived") for row in rows],
            )
        )

    versions.sort(key=_version_sort_key, reverse=True)
    return versions
