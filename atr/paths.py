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


def base_path_for_revision(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision: safe.RevisionNumber
) -> safe.StatePath:
    return get_unfinished_dir() / project_key / version_key / revision


def closer_download_url(relpath: safe.RelPath) -> str:
    # For the artifacts themselves we go via the mirror network rather than
    # downloads.apache.org. action=download skips the human mirror-picker page and
    # redirects straight to a chosen mirror.
    return f"{constants.CLOSER_LUA_URL}/{relpath}?action=download"


def committee_dist_relpath(
    committee: sql.Committee, suffix: safe.RelPath | None = None, filename: str | None = None
) -> safe.RelPath:
    # The committee's path relative to the downloads root, ie the shared prefix that
    # the download hosts and the distribution SVN area both hang a release off.
    # Optionally extended with the per-release download suffix and a file name.
    relpath = safe.RelPath.from_path(committee_downloads_dir(committee).path.relative_to(get_downloads_dir().path))
    if suffix is not None:
        relpath = relpath.append(suffix.as_path())
    if filename is not None:
        relpath = relpath.append(filename)
    return relpath


def committee_downloads_dir(committee: sql.Committee) -> safe.StatePath:
    downloads_dir = get_downloads_dir()
    if committee.is_podling:
        return downloads_dir / "incubator" / committee.key
    return downloads_dir / committee.key


def committee_downloads_url(host: str, committee: sql.Committee) -> str:
    # This is a slight extension of the intended paths concept
    # But URLs contain paths, so atr.paths does not have to be limited to filesystem paths
    if committee.is_podling:
        return f"https://{host}/downloads/incubator/{committee.key}"
    return f"https://{host}/downloads/{committee.key}"


def archive_download_url(relpath: safe.RelPath) -> str:
    # archive.apache.org keeps every release ever published, including those pruned from the
    # live mirror, so an archived release's files are served from here regardless of host.
    return f"{constants.ARCHIVE_APACHE_URL}/{relpath}"


def downloads_url(relpath: safe.RelPath) -> str:
    # downloads.apache.org itself, not a mirror. Signatures, checksums and KEYS
    # must come from here rather than a mirror, otherwise a bad mirror could serve
    # a matching artifact and signature.
    return f"{constants.DOWNLOADS_APACHE_URL}/{relpath}"


def get_archives_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().ARCHIVES_STORAGE_DIR))


def get_attestable_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().ATTESTABLE_STORAGE_DIR))


def get_downloads_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().DOWNLOADS_STORAGE_DIR))


def get_finished_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().FINISHED_STORAGE_DIR))


def get_finished_dir_for(project_key: safe.ProjectKey, version_key: safe.VersionKey) -> safe.StatePath:
    return get_finished_dir() / project_key / version_key


def get_quarantined_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().STATE_DIR) / "quarantined")


def get_tmp_dir() -> safe.StatePath:
    # This must be on the same filesystem as the other state subdirectories
    return safe.StatePath(pathlib.Path(config.get().STATE_DIR) / "temporary")


def get_unfinished_dir() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().UNFINISHED_STORAGE_DIR))


def get_unfinished_dir_for(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision: safe.RevisionNumber
) -> safe.StatePath:
    return get_unfinished_dir() / project_key / version_key / revision


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
    """Determine the filesystem directory for a given release based on its phase."""
    phase = release.phase
    project_key = release.project_key
    version_key = release.version

    base_dir: safe.StatePath | None = None
    match phase:
        case sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            base_dir = get_unfinished_dir()
        case sql.ReleasePhase.RELEASE_CANDIDATE:
            base_dir = get_unfinished_dir()
        case sql.ReleasePhase.RELEASE_PREVIEW:
            base_dir = get_unfinished_dir()
        case sql.ReleasePhase.RELEASE:
            base_dir = get_finished_dir()
        # Do not add "case _" here
    return base_dir / project_key / version_key


def release_directory_revision(release: sql.Release) -> safe.StatePath | None:
    """Return the path to the directory containing the active files for a given release phase."""
    path_project = release.project_key
    path_version = release.version
    match release.phase:
        case (
            sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
            | sql.ReleasePhase.RELEASE_CANDIDATE
            | sql.ReleasePhase.RELEASE_PREVIEW
        ):
            if (path_revision := release.latest_revision_number) is None:
                return None
            path = get_unfinished_dir() / path_project / path_version / path_revision
        case sql.ReleasePhase.RELEASE:
            path = get_finished_dir() / path_project / path_version
        # Do not add "case _" here
    return path


def release_directory_version(release: sql.Release) -> safe.StatePath:
    """Return the path to the directory containing the active files for a given release phase."""
    path_project = release.project_key
    path_version = release.version
    match release.phase:
        case (
            sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
            | sql.ReleasePhase.RELEASE_CANDIDATE
            | sql.ReleasePhase.RELEASE_PREVIEW
        ):
            path = get_unfinished_dir() / path_project / path_version
        case sql.ReleasePhase.RELEASE:
            path = get_finished_dir() / path_project / path_version
        # Do not add "case _" here
    return path


def revision_path_for_file(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision: safe.RevisionNumber, file_name: str
) -> safe.StatePath:
    return base_path_for_revision(project_key, version_key, revision) / file_name
