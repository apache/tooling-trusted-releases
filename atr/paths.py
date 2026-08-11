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

import pathlib

import atr.config as config
import atr.constants as constants
import atr.models.safe as safe
import atr.models.sql as sql


def archive_download_url(path: str | safe.RelPath) -> str:
    # archive.apache.org keeps every release ever published, including those pruned from the
    # live mirror, so an archived release's files are served from here regardless of host.
    return f"{constants.ARCHIVE_APACHE_URL}/{path}"


def audit_release_log_file(project_key: safe.ProjectKey, version_key: safe.VersionKey) -> safe.StatePath:
    base = safe.StatePath(pathlib.Path(config.get().STATE_DIR) / "audit" / "releases")
    return base / project_key / f"{version_key}.jsonl"


def base_path_for_revision(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber,
    embargoed: bool = False,
) -> safe.StatePath:
    # Callers without a Release object pass embargoed explicitly; it's always the release's
    # is_embargoed, so the root choice matches _release_root.
    return _unfinished_root(embargoed) / project_key / version_key / revision


def closer_download_url(path: str | safe.RelPath) -> str:
    # For the artifacts themselves we go via the mirror network rather than
    # downloads.apache.org. action=download skips the human mirror-picker page and
    # redirects straight to a chosen mirror.
    return f"{constants.CLOSER_LUA_URL}/{path}?action=download"


def committee_dist_relpath(
    committee: sql.Committee, suffix: safe.RelPath | None = None, filename: str | None = None
) -> safe.RelPath:
    # The committee's path relative to the distribution root, ie the shared prefix that
    # the download hosts and the distribution SVN area both hang a release off.
    # Optionally extended with the per-release download suffix and a file name.
    prefix = "incubator/" if committee.is_podling else ""
    relpath = safe.RelPath(f"{prefix}{committee.key}")
    if suffix is not None:
        relpath = relpath.append(suffix.as_path())
    if filename is not None:
        relpath = relpath.append(filename)
    return relpath


def committee_keys_url(committee: sql.Committee) -> str:
    return downloads_url(committee_dist_relpath(committee, filename="KEYS"))


def downloads_url(path: str | safe.RelPath) -> str:
    # downloads.apache.org itself, not a mirror. Signatures, checksums and KEYS
    # must come from here rather than a mirror, otherwise a bad mirror could serve
    # a matching artifact and signature.
    return f"{constants.DOWNLOADS_APACHE_URL}/{path}"


def get_archives_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().ARCHIVES_STORAGE_DIR))


def get_attestable_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().ATTESTABLE_STORAGE_DIR))


def get_catalog_site_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().CATALOG_SITE_DIR))


def get_embargoed_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().EMBARGOED_STORAGE_DIR))


def get_finished_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().FINISHED_STORAGE_DIR))


def get_finished_dir_for(project_key: safe.ProjectKey, version_key: safe.VersionKey) -> safe.StatePath:
    return get_finished_dir() / project_key / version_key


def get_quarantined_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().STATE_DIR) / "quarantined")


def get_runtime_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().STATE_DIR) / "runtime")


def get_tmp_dir() -> safe.StatePath:
    # This must be on the same filesystem as the other state subdirectories
    return safe.StatePath(pathlib.Path(config.get().STATE_DIR) / "temporary")


def get_unfinished_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().UNFINISHED_STORAGE_DIR))


def get_unfinished_dir_for(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber,
    embargoed: bool = False,
) -> safe.StatePath:
    return base_path_for_revision(project_key, version_key, revision, embargoed=embargoed)


def get_unfinished_tombstone_for(project_key: safe.ProjectKey, version_key: safe.VersionKey) -> safe.StatePath:
    return get_unfinished_dir() / project_key / f"{version_key}.deleting-"


def quarantine_directory(quarantined: sql.Quarantined) -> safe.StatePath:
    if not quarantined.token.isalnum():
        raise ValueError("Invalid quarantine token")
    release = quarantined.release
    return get_quarantined_dir() / release.project_key / release.version / quarantined.token


def release_directory(release: sql.Release) -> safe.StatePath:
    """Return the absolute path to the directory containing the active files for a given release phase."""
    latest_revision_number = release.latest_revision_number
    if (release.phase == sql.ReleasePhase.RELEASE) or (latest_revision_number is None):
        return release_directory_base(release)
    return release_directory_base(release) / latest_revision_number


def release_directory_base(release: sql.Release) -> safe.StatePath:
    """Determine the filesystem directory for a given release based on its phase and embargo status."""
    return _release_root(release) / release.project_key / release.version


def release_directory_revision(release: sql.Release) -> safe.StatePath | None:
    """Return the path to the directory containing the active files for a given release phase."""
    base = _release_root(release) / release.project_key / release.version
    # A released release has no revision subdirectory; everything earlier is revision-scoped.
    if release.phase == sql.ReleasePhase.RELEASE:
        return base
    if (path_revision := release.latest_revision_number) is None:
        return None
    return base / path_revision


def release_directory_version(release: sql.Release) -> safe.StatePath:
    """Return the path to the directory containing the active files for a given release phase."""
    return _release_root(release) / release.project_key / release.version


def revision_path_for_file(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber,
    file_name: str,
    embargoed: bool = False,
) -> safe.StatePath:
    return base_path_for_revision(project_key, version_key, revision, embargoed=embargoed) / file_name


def _release_root(release: sql.Release) -> safe.StatePath:
    # The phase decides the tree: a release's files sit in the unfinished area until it's
    # published, and in the finished area thereafter. Embargo only shifts the pre-publication
    # root, so it applies within the unfinished cases and never to a published release.
    base_dir: safe.StatePath | None = None
    match release.phase:
        case sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            base_dir = _unfinished_root(release.is_embargoed)
        case sql.ReleasePhase.RELEASE_CANDIDATE:
            base_dir = _unfinished_root(release.is_embargoed)
        case sql.ReleasePhase.RELEASE_PREVIEW:
            base_dir = _unfinished_root(release.is_embargoed)
        case sql.ReleasePhase.RELEASE:
            base_dir = get_finished_dir()
        # Do not add "case _" here
    return base_dir


def _unfinished_root(embargoed: bool) -> safe.StatePath:
    # Before publication a release's files live in the unfinished tree, unless it's embargoed,
    # in which case they're kept apart in the embargoed tree.
    return get_embargoed_dir() if embargoed else get_unfinished_dir()
