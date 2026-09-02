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
from collections.abc import Iterable, Sequence
from typing import Final, Literal

import packaging.version as version

import atr.cycles as cycles
import atr.models as models
import atr.models.safe as safe
import atr.models.sql as sql
import atr.util as util

_GROUPED_METHODS: Final[frozenset[sql.VersionMethod]] = frozenset({sql.VersionMethod.SEMVER, sql.VersionMethod.CALVER})


@dataclasses.dataclass
class ArtifactWindow:
    versions: list[str]
    skip: int
    count: int


@dataclasses.dataclass
class CatalogProject:
    versions: list[models.api.CatalogVersion]
    cycles: list[models.api.CatalogCycle]
    grouped: bool


@dataclasses.dataclass
class CycleGroup:
    """A cycle and its versions, ready to render.

    `label` is None when the project has nothing worth heading - a lone default
    cycle - so a template iterates without carrying the rule itself.
    """

    label: str | None
    lifecycle: str | None
    versions: Sequence[models.api.CatalogVersion]


def artifact_window(
    version_method: sql.VersionMethod,
    rows: Sequence[tuple[str, datetime.datetime | None, int | None, int]],
    offset: int,
    limit: int,
) -> ArtifactWindow:
    counts = {row[0]: row[3] for row in rows}
    versions: list[str] = []
    skip = 0
    end = 0
    for version_key in version_order(version_method, [row[:3] for row in rows]):
        start = end
        end += counts[version_key]
        if end <= offset:
            continue
        if start >= (offset + limit):
            break
        if not versions:
            skip = offset - start
        versions.append(version_key)
    return ArtifactWindow(versions=versions, skip=skip, count=sum(counts.values()))


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
    versions = _versions(artifacts, cycles_by_key, version_method, atr_host)
    grouped = _grouped_layout(version_method)
    if grouped:
        # Cycle labels are unique within a project, so they group and look up safely.
        cycles_by_label = {cycle.cycle: cycle for cycle in project_cycles}
        catalog_cycles = _cycles(versions, cycles_by_label, now)
    else:
        catalog_cycles = []
    return CatalogProject(versions=versions, cycles=catalog_cycles, grouped=grouped)


def cle_project_url(atr_host: str, project_key: str) -> str:
    return f"https://{atr_host}/api/cle/project/{project_key}"


def cle_release_url(atr_host: str, project_key: str, version: str) -> str:
    return f"https://{atr_host}/api/cle/release/{project_key}/{version}"


def clip_versions(
    versions: Sequence[models.api.CatalogVersion], window: ArtifactWindow, limit: int
) -> list[models.api.CatalogVersion]:
    ranks = {key: index for index, key in enumerate(window.versions)}
    clipped = sorted(versions, key=lambda entry: ranks[str(entry.version)])
    skip = window.skip
    remaining = limit
    for entry in clipped:
        artifacts = sorted(entry.artifacts, key=lambda artifact: artifact.artifact_path)
        entry.artifacts = artifacts[skip : (skip + remaining)]
        remaining -= len(entry.artifacts)
        skip = 0
    return clipped


def cycle_groups(
    versions: Sequence[models.api.CatalogVersion],
    project_cycles: Sequence[sql.ProjectCycle],
    now: datetime.datetime,
    *,
    order: Sequence[str] | None = None,
) -> list[CycleGroup]:
    """Group versions into their cycles for display, most recently active first.

    `assemble` groups every version of a project at once and returns the API shape.
    Pages regroup here instead, so that a caller which has split the versions -
    releases on one page, archives on another - labels each side by the same rule.
    """
    grouped = _cycles(versions, {cycle.cycle: cycle for cycle in project_cycles}, now)
    if order is not None:
        ranks = {key: index for index, key in enumerate(order)}
        grouped.sort(key=lambda entry: ranks[str(entry.versions[0].version)])
    headed = cycles.headings_needed(entry.cycle for entry in grouped)
    return [
        CycleGroup(
            label=cycles.display_name(entry.cycle) if headed else None,
            lifecycle=entry.lifecycle,
            versions=entry.versions,
        )
        for entry in grouped
    ]


def version_order(
    version_method: sql.VersionMethod, items: Iterable[tuple[str, datetime.datetime | None, int | None]]
) -> list[str]:
    entries = list(items)
    if _grouped_layout(version_method):
        ranks = _version_ranks(entry[0] for entry in entries)
        return [entry[0] for entry in sorted(entries, key=lambda entry: ranks[entry[0]], reverse=True)]
    return [entry[0] for entry in sorted(entries, key=_version_date_key, reverse=True)]


def _artifact(row: sql.Artifact, downloadable: bool, archived: bool) -> models.api.CatalogArtifact:
    artifact_url: str | None = None
    signature_url: str | None = None
    checksum_url: str | None = None
    sbom_url: str | None = None
    if downloadable:
        # The stored suffix is the file's real dist location, so a moved project's artifacts
        # (graduated from an umbrella, or shifted to the Attic) resolve where they actually live. The
        # signature, checksum and SBOM share the directory. A row with no recorded suffix roots at
        # the bare file path.
        suffix = row.download_path_suffix

        def _dist_url(rel_path: str, kind: util.DownloadFile) -> str:
            path = f"{suffix}/{rel_path}" if suffix else rel_path
            return util.download_url_for_published_path(path, kind, archived=archived)

        artifact_url = _dist_url(row.artifact_path, util.DownloadFile.ARTIFACT)
        if row.signature_path:
            signature_url = _dist_url(row.signature_path, util.DownloadFile.METADATA)
        if row.checksum_path:
            checksum_url = _dist_url(row.checksum_path, util.DownloadFile.METADATA)
        if row.sbom_path:
            sbom_url = _dist_url(row.sbom_path, util.DownloadFile.METADATA)
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


def _cycles(
    versions: Sequence[models.api.CatalogVersion],
    cycles_by_label: dict[str, sql.ProjectCycle],
    now: datetime.datetime,
) -> list[models.api.CatalogCycle]:
    by_label: dict[str, list[models.api.CatalogVersion]] = collections.defaultdict(list)
    for entry in versions:
        if entry.cycle is None:
            continue
        by_label[entry.cycle].append(entry)

    catalog = [
        models.api.CatalogCycle(cycle=label, lifecycle=_lifecycle_badge(cycles_by_label[label], now), versions=grouped)
        for label, grouped in by_label.items()
    ]
    # A cycle label is a version prefix (2.26, 3.0), so order the cycles by that, newest first - only
    # grouped (scheme) projects reach here, and a scheme means the version, not the date, is the order
    ranks = _version_ranks(by_label.keys())
    catalog.sort(key=lambda c: ranks[c.cycle], reverse=True)
    return catalog


def _grouped_layout(version_method: sql.VersionMethod) -> bool:
    # Simple projects have only the default cycle, so they skip cycle grouping.
    return version_method in _GROUPED_METHODS


def _lifecycle_badge(cycle: sql.ProjectCycle, now: datetime.datetime) -> str | None:
    # The default cycle is the catch-all for projects without cycle_match. It carries no
    # lifecycle dates, so it has no state to report either.
    if cycle.cycle == cycles.DEFAULT_CYCLE:
        return None
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


def _loose_version_key(text: str) -> tuple[tuple[int, int | str], ...]:
    # A version split into numeric-then-string parts, for ordering strings PEP 440 rejects. Numbers
    # sort ahead of strings so 1.0 leads 1.0-rc1, matching the fallback releases_by_project uses
    parts: list[tuple[int, int | str]] = []
    for part in text.replace("+", ".").replace("-", ".").split("."):
        try:
            parts.append((0, int(part)))
        except ValueError:
            parts.append((1, part))
    return tuple(parts)


def _version_date_key(entry: tuple[str, datetime.datetime | None, int | None]) -> tuple[datetime.datetime, int, str]:
    # Newest first: released date leads, svn revision breaks ties for older rows.
    released = entry[1] or datetime.datetime.min.replace(tzinfo=datetime.UTC)
    return (released, entry[2] or 0, entry[0])


def _version_ranks(labels: Iterable[str]) -> dict[str, int]:
    # Rank distinct version strings (or cycle labels, which are version prefixes) low to high, so a
    # caller sorts newest first by reversing. PEP 440 first; one string it rejects drops the whole set
    # to the loose split, so an odd tag can't throw the order or raise
    distinct = list(set(labels))
    try:
        ordered = sorted(distinct, key=lambda label: (version.Version(label), label))
    except version.InvalidVersion:
        ordered = sorted(distinct, key=lambda label: (_loose_version_key(label), label))
    return {label: index for index, label in enumerate(ordered)}


def _versions(
    artifacts: Sequence[sql.Artifact],
    cycles_by_key: dict[str, sql.ProjectCycle],
    version_method: sql.VersionMethod,
    atr_host: str | None = None,
) -> list[models.api.CatalogVersion]:
    by_version: dict[safe.VersionKey, list[sql.Artifact]] = collections.defaultdict(list)
    for artifact in artifacts:
        by_version[artifact.safe_version_key].append(artifact)

    versions: list[models.api.CatalogVersion] = []
    for version_key, rows in by_version.items():
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
                version=version_key,
                status=status,
                released=(release.released or release.created) if (release is not None) else None,
                svn_revision=max(svn_revisions) if svn_revisions else None,
                managed=any(row.managed for row in rows),
                cycle=cycle.cycle if (cycle is not None) else None,
                cle_url=cle_release_url(atr_host, release.project_key, str(version_key))
                if (atr_host is not None) and cle_eligible and (release is not None)
                else None,
                vote_thread_url=release.vote_thread_url if (release is not None) else None,
                artifacts=[_artifact(row, downloadable, status == "archived") for row in rows],
            )
        )

    # A project with a version scheme (semver/calver) orders by the version itself, so a later patch
    # of an older line can't jump a newer line the way a date sort lets it. A simple project has no
    # scheme, so it keeps the by-date order its lone default cycle wants
    order = version_order(
        version_method, [(str(entry.version), entry.released, entry.svn_revision) for entry in versions]
    )
    ranks = {key: index for index, key in enumerate(order)}
    versions.sort(key=lambda entry: ranks[str(entry.version)])
    return versions
