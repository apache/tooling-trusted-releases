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
Decompose dist paths into project and version, and catalogue dist commits.
"""

import dataclasses
import datetime
import pathlib
import re
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

# Changed paths are relative to the dist repo root
# We only watch published releases; dev candidate activity is out of scope
_RELEASE_PREFIX: Final[str] = "release/"

# Verified dist-path -> projects.json key remaps; subproject is None for a committee-level layout
# Per-entry only - a blanket rule gives false positives (camel-karaf is Camel's, not Karaf)
DIST_PROJECT_REMAPS: Final[dict[tuple[str, str | None], str]] = {
    # Artemis graduated from ActiveMQ; dist still splits it across both areas
    ("activemq", "activemq-artemis"): "artemis",
    # httpd's top level is the HTTP Server, and the committee has two projects.json
    # entries so the bare-name rule can't pick
    ("httpd", None): "httpd-http_server",
    # Dist names that differ too much from the projects.json key for the bare-name match
    ("trafficcontrol", None): "attic-traffic-control",
    ("xmlgraphics", "commons"): "xmlgraphics-xml-graphics-commons",
}

# Wheels ship beside the source tarball (airflow providers), so they're a build, not a release
IGNORED_ARTIFACT_SUFFIXES: Final[tuple[str, ...]] = (".whl",)

# Companion files paired to an artifact by basename, strongest first
_SIGNATURE_SUFFIXES: Final[tuple[str, ...]] = (".asc", ".sig")
_CHECKSUM_SUFFIXES: Final[tuple[str, ...]] = (".sha512", ".sha256", ".sha1", ".sha", ".mds", ".md5")
_SBOM_SUFFIXES: Final[tuple[str, ...]] = (".cdx.json", ".cdx.xml")

# A version-ish component; anything it misses falls through to filename parsing
_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"^v?\d+(?:\.\d+)*(?:[.\-_][A-Za-z0-9]+)*$")

# A bare single digit is a major-version grouping dir (xerces/c/2), not a version
_BARE_DIGIT_RE: Final[re.Pattern[str]] = re.compile(r"^\d$")

# Splits a flat name-version; non-greedy, so spark-4.1.0-preview1 comes apart at the version
_COMBINED_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<name>.+?)-(?P<version>v?\d+(?:\.\d+)*(?:[.\-][A-Za-z0-9]+)*)$")

# Semver in a filename: dotted numerics plus an ASF qualifier tail. The lookbehind blocks
# only a leading digit, so a dotted classifier (Xerces-J-bin.2.10.0) still parses; underscore
# isn't a separator
_SEMVER_FILE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![0-9])(\d+(?:\.\d+)+(?:[.\-](?:alpha|beta|rc|cr|m|milestone|preview|pre|incubating|final|ga)[0-9]*)*)",
    re.IGNORECASE,
)

# Calver dates (2026-01-27 or 20050330); tried only when there's no semver, and
# limited to 19xx/20xx so we don't grab an arbitrary run of digits
_CALVER_FILE_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2}|(?:19|20)\d{6})(?!\d)")

# A release resolved against ATR's projects, ready to hand to the system actor
type _ResolvedRelease = tuple[safe.ProjectKey, safe.VersionKey, list[release.ArtifactInput]]
type _ResolvedArchive = tuple[safe.ProjectKey, safe.VersionKey]


@dataclasses.dataclass(frozen=True)
class Decomposed:
    subproject: str | None
    version: str | None
    # "dir", "combined", "filename" or "unknown"
    source: str


@dataclasses.dataclass
class _ReleaseFiles:
    # Added files for one release in a commit as (dir-under-release, basename, classification),
    # so artifacts pair with companions in the same directory and each file is classified once
    committee: str
    subproject: str | None
    version: str
    files: list[tuple[str, str, classify.FileType]] = dataclasses.field(default_factory=list)
    has_source: bool = False


def candidate_keys(committee: str, subproject: str | None) -> list[str]:
    # Dist layout and projects.json keys don't always line up (commons/lang -> commons-lang, but
    # accumulo/accumulo-access is already its key), so try the obvious shapes; apache- is stripped
    if not subproject:
        return [committee]
    bare = subproject.removeprefix("apache-")
    keys: list[str] = []
    for name in dict.fromkeys([bare, subproject]):
        keys += [name, f"{committee}-{name}"]
    return keys


async def catalogue_commit(commit: dict) -> None:
    if str(commit.get("committer", "")) == svn.ASF_TOOL:
        # Our own publishes are already in the database
        return
    changed = commit.get("changed", {})
    if not isinstance(changed, dict):
        log.warning(f"dist commit payload has unexpected changed shape: {type(changed).__name__}")
        return
    added, removed = _structural_changes(changed)
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
    async with storage.write_as_system() as system:
        await _apply_changes(system, releases, archives, date)


def decompose(committee: str, parts: tuple[str, ...], filename: str | None = None) -> Decomposed | None:
    # parts are the dirs below the committee, filename the release file if any. The filename
    # wins for the version (a .../4.5/ series dir covers 4.5.x; the file pins 4.5.13), dir is the fallback
    parts = _strip_self_prefix(committee, parts)
    if not parts:
        return _filename_only(committee, filename)

    subproject, dir_version, dir_source = _subproject_and_dir_version(committee, parts)
    name_version = version_from_filename(filename) if filename else None
    if name_version is not None:
        return Decomposed(subproject=subproject, version=name_version, source="filename")
    return Decomposed(subproject=subproject, version=dir_version, source=dir_source)


def missing_project_key(committee: str, subproject: str | None) -> str:
    # An ATR-style key for a project projects.json lacks, so releases can FK to it
    # apache- stripped, and we avoid doubling the committee prefix
    if not subproject:
        return committee
    bare = subproject.removeprefix("apache-")
    if bare.startswith(f"{committee}-"):
        return bare
    return f"{committee}-{bare}"


def normalise(name: str) -> str:
    # So traffic_server, traffic-server and "Traffic Server" all compare equal
    return re.sub(r"[^a-z0-9]", "", name.lower())


def version_from_filename(filename: str) -> str | None:
    # Semver wins over calver so a dotted release isn't misread as a date
    semver = _SEMVER_FILE_RE.search(filename)
    if semver is not None:
        return semver.group(1)
    calver = _CALVER_FILE_RE.search(filename)
    if calver is not None:
        return calver.group(1)
    return None


async def _apply_changes(
    system: release.FoundationAdmin,
    releases: list[_ResolvedRelease],
    archives: list[_ResolvedArchive],
    date: datetime.datetime,
) -> None:
    for project_key, version_key, artifacts in releases:
        error = await system.catalogue_release(project_key, version_key, date, artifacts)
        if error is not None:
            log.warning(f"dist watcher could not catalogue {project_key!s} {version_key!s}: {error}")
    for project_key, version_key in archives:
        error = await system.archive(project_key, version_key)
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
        if (not analysis.is_artifact(name)) or name.endswith(IGNORED_ARTIFACT_SUFFIXES):
            continue
        seen.add(name)
        siblings = by_dir[dirpath]
        artifacts.append(
            release.ArtifactInput(
                artifact_path=name,
                classification=file_type.value,
                signature_path=_companion(siblings, name, _SIGNATURE_SUFFIXES),
                checksum_path=_companion(siblings, name, _CHECKSUM_SUFFIXES),
                sbom_path=_companion(siblings, name, _SBOM_SUFFIXES),
                download_path_suffix="/".join(dirpath.split("/")[1:]) or None,
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


def _decompose_change(path: str) -> tuple[str, Decomposed, bool, str | None] | None:
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
        decomposed = decompose(committee, tuple(rest), None)
        filename = None
    else:
        filename = rest[-1]
        decomposed = decompose(committee, tuple(rest[:-1]), filename)
    if decomposed is None:
        return None
    return committee, decomposed, is_dir, filename


def _filename_only(committee: str, filename: str | None) -> Decomposed | None:
    # A file directly under the committee has no dir to name the subproject, so it comes from
    # the filename before the version (sling does this); a name == committee is a TLP release
    if not filename:
        return None
    version = version_from_filename(filename)
    if version is None:
        return None
    name = filename[: filename.find(version)].rstrip("-._")
    if (not name) or (name.removeprefix("apache-") == committee):
        return Decomposed(subproject=None, version=version, source="filename")
    return Decomposed(subproject=name, version=version, source="filename")


def _is_version_dir(component: str) -> bool:
    if _BARE_DIGIT_RE.match(component):
        return False
    return bool(_VERSION_RE.match(component))


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
    if (release_record is None) or (release_record.archived is not None):
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


async def _resolve_project(data: db.Session, committee: str, subproject: str | None) -> sql.Project | None:
    # Resolve against what ATR already has, via the same remaps and candidate keys
    # as the backfill. The watcher never invents projects; unknowns are logged
    remapped = DIST_PROJECT_REMAPS.get((committee, subproject))
    candidates = [remapped] if remapped is not None else candidate_keys(committee, subproject)
    for candidate in candidates:
        project = await data.project(key=candidate).get()
        if project is not None:
            return project
    return None


def _safe_keys(project_key: str, version: str) -> tuple[safe.ProjectKey, safe.VersionKey] | None:
    try:
        return safe.ProjectKey(project_key), safe.VersionKey(version)
    except ValueError:
        log.warning(f"dist commit version {version!r} for {project_key} is not a valid version key; skipping")
        return None


def _split_combined(component: str) -> tuple[str, str] | None:
    match = _COMBINED_RE.match(component)
    if match is None:
        return None
    return match.group("name"), match.group("version")


def _strip_self_prefix(committee: str, parts: tuple[str, ...]) -> tuple[str, ...]:
    # jackrabbit/jackrabbit/... and ace/apache-ace/... are the TLP's own area, not
    # a subproject. Drop a leading dir that just repeats the committee
    if parts and (parts[0] in (committee, f"apache-{committee}")):
        return parts[1:]
    return parts


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


def _subproject_and_dir_version(committee: str, parts: tuple[str, ...]) -> tuple[str | None, str | None, str]:
    # First dir decides the subproject; the version is whichever dir looks like one
    first = parts[0]
    if _is_version_dir(first):
        return None, first, "dir"
    combined = _split_combined(first)
    if combined is not None:
        name, version = combined
        return (None if name == committee else name), version, "combined"
    for part in parts[1:]:
        if _is_version_dir(part):
            return first, part, "dir"
        deeper = _split_combined(part)
        if deeper is not None:
            return first, deeper[1], "combined"
    return first, None, "unknown"
