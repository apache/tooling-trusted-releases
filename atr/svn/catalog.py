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

"""
Catalogue published dist commits into the database via the storage interface.
"""

import dataclasses
import datetime
import pathlib
from typing import Final

import atr.analysis as analysis
import atr.classify as classify
import atr.config as config
import atr.db as db
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.release as release
import atr.svn as svn
import atr.svn.dist as dist

# Changed paths are relative to the dist repo root
# We only watch published releases; dev candidate activity is out of scope
_RELEASE_PREFIX: Final[str] = "release/"

# Companion files paired to an artifact by basename, strongest first
_SIGNATURE_SUFFIXES: Final[tuple[str, ...]] = (".asc", ".sig")
_CHECKSUM_SUFFIXES: Final[tuple[str, ...]] = (".sha512", ".sha256", ".sha1", ".sha", ".mds", ".md5")
_SBOM_SUFFIXES: Final[tuple[str, ...]] = (".cdx.json", ".cdx.xml")

# A release resolved against ATR's projects, ready to hand to the system actor
type _ResolvedRelease = tuple[safe.ProjectKey, safe.VersionKey, list[release.ArtifactInput]]
type _ResolvedArchive = tuple[safe.ProjectKey, safe.VersionKey]


@dataclasses.dataclass
class _ReleaseFiles:
    # Added files for one release in a commit as (dir-under-release, basename, classification),
    # so artifacts pair with companions in the same directory and each file is classified once
    committee: str
    subproject: str | None
    version: str
    files: list[tuple[str, str, classify.FileType]] = dataclasses.field(default_factory=list)
    has_source: bool = False


async def catalogue_commit(commit: dict) -> None:
    if str(commit.get("committer", "")) == svn.ASF_TOOL:
        # Our own publishes are already in the database
        return
    changed = commit.get("changed", {})
    if not isinstance(changed, dict):
        log.warning(f"dist commit payload has unexpected changed shape: {type(changed).__name__}")
        return
    added, removed = _structural_changes(changed)
    added = _collapse_airflow_providers(added)
    if not (added or removed):
        return
    date = _commit_date(commit)
    # Resolve against ATR's projects in a read session first, so decomposition and
    # project lookups stay out of the storage writer
    async with db.session() as data:
        releases, archives = await _resolve_changes(data, added, removed)
    # Report mode (the default) just logs what it would do; active mode hands
    # the resolved work to the system actor
    if not config.get().DIST_CATALOG_WRITE:
        _report(releases, archives, date)
        return
    async with storage.write_as_system(storage.WriteAsDistCatalogService) as wadcs:
        await _apply_changes(wadcs, releases, archives, date)


async def _apply_changes(
    wadcs: storage.WriteAsDistCatalogService,
    releases: list[_ResolvedRelease],
    archives: list[_ResolvedArchive],
    date: datetime.datetime,
) -> None:
    for project_key, version_key, artifacts in releases:
        error = await wadcs.release_catalogue_release(project_key, version_key, date, artifacts)
        if error is not None:
            log.warning(f"dist watcher could not catalogue {project_key!s} {version_key!s}: {error}")
    for project_key, version_key in archives:
        error = await wadcs.release_archive(project_key, version_key)
        if error is not None:
            log.info(f"dist watcher did not archive {project_key!s} {version_key!s}: {error}")


def _artifacts(rel_files: _ReleaseFiles) -> list[release.ArtifactInput]:
    # One input per real artifact file (source/binary/docs); companions are paired
    # from the same directory in this commit, and left null when they're not here
    by_dir: dict[str, set[str]] = {}
    for dirpath, name, _ in rel_files.files:
        by_dir.setdefault(dirpath, set()).add(name)
    artifacts: list[release.ArtifactInput] = []
    seen: set[str] = set()
    for dirpath, name, file_type in rel_files.files:
        if (name in seen) or (file_type in (classify.FileType.METADATA, classify.FileType.DISALLOWED)):
            continue
        if (not analysis.is_artifact(name)) or name.endswith(dist.IGNORED_ARTIFACT_SUFFIXES):
            continue
        seen.add(name)
        siblings = by_dir[dirpath]
        artifacts.append(
            release.ArtifactInput(
                artifact_path=name,
                classification=file_type.value,
                download_path_suffix=dirpath,
                signature_path=_companion(siblings, name, _SIGNATURE_SUFFIXES),
                checksum_path=_companion(siblings, name, _CHECKSUM_SUFFIXES),
                sbom_path=_companion(siblings, name, _SBOM_SUFFIXES),
            )
        )
    return artifacts


def _commit_date(commit: dict) -> datetime.datetime:
    # svn dates look like "2026-06-12 15:23:41 +0000 (Fri, 12 Jun 2026)"
    head = str(commit.get("date", "")).split(" (", 1)[0]
    try:
        return datetime.datetime.strptime(head, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return datetime.datetime.now(datetime.UTC)


def _companion(siblings: set[str], artifact: str, suffixes: tuple[str, ...]) -> str | None:
    for suffix in suffixes:
        if (artifact + suffix) in siblings:
            return artifact + suffix
    return None


def _decompose_change(path: str) -> tuple[str, dist.Decomposed, bool, str | None] | None:
    # release/<committee>/... only; returns committee, decomposition, is_dir and
    # the filename (None for a directory)
    if not path.startswith(_RELEASE_PREFIX):
        return None
    is_dir = path.endswith("/")
    rel = path.removeprefix(_RELEASE_PREFIX).rstrip("/")
    if not rel:
        return None
    parts = rel.split("/")
    committee = parts[0]
    rest = parts[1:]
    if not rest:
        return None
    if is_dir:
        decomposed = dist.decompose(committee, tuple(rest), None)
        filename = None
    else:
        filename = rest[-1]
        decomposed = dist.decompose(committee, tuple(rest[:-1]), filename)
    if decomposed is None:
        return None
    return committee, decomposed, is_dir, filename


def _report(
    releases: list[_ResolvedRelease],
    archives: list[_ResolvedArchive],
    date: datetime.datetime,
) -> None:
    for project_key, version_key, artifacts in releases:
        log.info(
            f"dist watcher (report mode) would catalogue release {project_key!s} {version_key!s} "
            f"(released {date.date()}) with {len(artifacts)} artifact(s)"
        )
    for project_key, version_key in archives:
        log.info(
            f"dist watcher (report mode) would archive release {project_key!s} {version_key!s} as of {date.date()}"
        )


async def _resolve_archive(
    data: db.Session, committee: str, subproject: str | None, version: str
) -> _ResolvedArchive | None:
    project = await _resolve_project(data, committee, subproject)
    if project is None:
        return None
    release_record = await data.release(key=f"{project.key}-{version}").get()
    if (release_record is None) or release_record.is_archived:
        return None
    return _safe_keys(project.key, version)


async def _resolve_changes(
    data: db.Session,
    added: dict[tuple[str, str | None, str], _ReleaseFiles],
    removed: set[tuple[str, str | None, str]],
) -> tuple[list[_ResolvedRelease], list[_ResolvedArchive]]:
    releases: list[_ResolvedRelease] = []
    for rel_files in sorted(added.values(), key=lambda r: (r.committee, r.subproject or "", r.version)):
        resolved = await _resolve_release(data, rel_files)
        if resolved is not None:
            releases.append(resolved)
    archives: list[_ResolvedArchive] = []
    for committee, subproject, version in sorted(removed, key=lambda k: (k[0], k[1] or "", k[2])):
        resolved_archive = await _resolve_archive(data, committee, subproject, version)
        if resolved_archive is not None:
            archives.append(resolved_archive)
    return releases, archives


async def _resolve_project(data: db.Session, committee: str, subproject: str | None) -> sql.Project | None:
    # Resolve against what ATR already has, via the same remaps and candidate keys
    # as the backfill. The watcher never invents projects; unknowns are logged
    remapped = dist.PROJECT_REMAPS.get((committee, subproject))
    candidates = [remapped] if remapped is not None else dist.candidate_keys(committee, subproject)
    for candidate in candidates:
        project = await data.project(key=candidate).get()
        if project is not None:
            return project
    return None


async def _resolve_release(data: db.Session, rel_files: _ReleaseFiles) -> _ResolvedRelease | None:
    version = rel_files.version
    project = await _resolve_project(data, rel_files.committee, rel_files.subproject)
    if project is None:
        log.info(f"dist commit for unknown project: {rel_files.committee}/{rel_files.subproject or ''} {version}")
        return None
    if await data.release(key=f"{project.key}-{version}").get() is not None:
        # Already catalogued, whether by ATR or an earlier watcher pass
        return None
    keys = _safe_keys(project.key, version)
    if keys is None:
        return None
    project_key, version_key = keys
    return project_key, version_key, _artifacts(rel_files)


def _safe_keys(project_key: str, version: str) -> tuple[safe.ProjectKey, safe.VersionKey] | None:
    try:
        return safe.ProjectKey(project_key), safe.VersionKey(version)
    except ValueError:
        log.warning(f"dist commit version {version!r} for {project_key} is not a valid version key; skipping")
        return None


def _structural_changes(
    changed: dict,
) -> tuple[dict[tuple[str, str | None, str], _ReleaseFiles], set[tuple[str, str | None, str]]]:
    # Group added files by release so we can record their artifacts, and collect
    # version-dir deletions as archivals. A release is only identified by an added
    # source artifact; binary/doc/metadata files are included but don't make a release
    added: dict[tuple[str, str | None, str], _ReleaseFiles] = {}
    removed: set[tuple[str, str | None, str]] = set()
    for raw_path, info in changed.items():
        path = str(raw_path)
        flags = str(info.get("flags", "")) if isinstance(info, dict) else ""
        change = _decompose_change(path)
        if change is None:
            continue
        committee, decomposed, is_dir, filename = change
        if decomposed.version is None:
            continue
        key = (committee, decomposed.subproject, decomposed.version)
        if flags.startswith("D") and is_dir:
            removed.add(key)
        elif flags.startswith("A") and (filename is not None):
            bundle = added.get(key)
            if bundle is None:
                bundle = _ReleaseFiles(committee, decomposed.subproject, decomposed.version)
                added[key] = bundle
            rel = path.removeprefix(_RELEASE_PREFIX)
            dirpath = rel.rsplit("/", 1)[0] if "/" in rel else ""
            file_type = classify.classify_path(pathlib.PurePosixPath(rel))
            bundle.files.append((dirpath, filename, file_type))
            if file_type is classify.FileType.SOURCE:
                bundle.has_source = True
    return {key: bundle for key, bundle in added.items() if bundle.has_source}, removed


def _airflow_bundle_area(bundle: _ReleaseFiles) -> str | None:
    if bundle.committee != "airflow":
        return None
    for dirpath, _name, _kind in bundle.files:
        # dirpath is under the release root and leads with the committee, so drop that segment
        area = dist.airflow_provider_area("airflow", tuple(dirpath.split("/")[1:]))
        if area is not None:
            return area
    return None


def _airflow_file_kind(name: str, kind: classify.FileType) -> classify.FileType:
    # The batch source stays the release's source; a provider package that classified as source is a
    # binary of the batch, and companions keep their kind
    if dist.airflow_calver_date(name) is not None:
        return classify.FileType.SOURCE
    if kind is classify.FileType.SOURCE:
        return classify.FileType.BINARY
    return kind


def _collapse_airflow_providers(
    added: dict[tuple[str, str | None, str], _ReleaseFiles],
) -> dict[tuple[str, str | None, str], _ReleaseFiles]:
    # Airflow ships its providers as a calver batch: a dated source plus that day's provider
    # packages, flat in one dir. Collapse the per-provider bundles this commit produced into one
    # release per area keyed by the batch's calver date, source kept as source and the rest binaries.
    # A commit with no batch source is left as it decomposed
    date_by_area: dict[str, str] = {}
    keys_by_area: dict[str, list[tuple[str, str | None, str]]] = {}
    for key, bundle in added.items():
        area = _airflow_bundle_area(bundle)
        if area is None:
            continue
        keys_by_area.setdefault(area, []).append(key)
        for _dirpath, name, _kind in bundle.files:
            date = dist.airflow_calver_date(name)
            if date is not None:
                date_by_area[area] = date
    for area, date in date_by_area.items():
        merged = _ReleaseFiles("airflow", area, date)
        for key in keys_by_area[area]:
            for dirpath, name, kind in added.pop(key).files:
                merged_kind = _airflow_file_kind(name, kind)
                merged.files.append((dirpath, name, merged_kind))
                if merged_kind is classify.FileType.SOURCE:
                    merged.has_source = True
        added[("airflow", area, date)] = merged
    return added
