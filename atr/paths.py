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

from atr import config as config
from atr.models import sql as sql


def base_path_for_revision(project_name: str, version_name: str, revision: str) -> pathlib.Path:
    return pathlib.Path(get_unfinished_dir(), project_name, version_name, revision)


def revision_path_for_file(project_name: str, version_name: str, revision: str, file_name: str) -> pathlib.Path:
    return base_path_for_revision(project_name, version_name, revision) / file_name


def get_attestable_dir() -> pathlib.Path:
    return pathlib.Path(config.get().ATTESTABLE_STORAGE_DIR)


def get_downloads_dir() -> pathlib.Path:
    return pathlib.Path(config.get().DOWNLOADS_STORAGE_DIR)


def get_finished_dir() -> pathlib.Path:
    return pathlib.Path(config.get().FINISHED_STORAGE_DIR)


def get_quarantined_dir() -> pathlib.Path:
    return pathlib.Path(config.get().STATE_DIR) / "quarantined"


def get_tmp_dir() -> pathlib.Path:
    # This must be on the same filesystem as the other state subdirectories
    return pathlib.Path(config.get().STATE_DIR) / "temporary"


def get_unfinished_dir() -> pathlib.Path:
    return pathlib.Path(config.get().UNFINISHED_STORAGE_DIR)


def get_upload_staging_dir(session_token: str) -> pathlib.Path:
    if not session_token.isalnum():
        raise ValueError("Invalid session token")
    return get_tmp_dir() / "upload-staging" / session_token


def quarantine_directory(quarantined: sql.Quarantined) -> pathlib.Path:
    if not quarantined.token.isalnum():
        raise ValueError("Invalid quarantine token")
    release = quarantined.release
    return get_quarantined_dir() / release.project_name / release.version / quarantined.token


def release_directory(release: sql.Release) -> pathlib.Path:
    """Return the absolute path to the directory containing the active files for a given release phase."""
    latest_revision_number = release.latest_revision_number
    if (release.phase == sql.ReleasePhase.RELEASE) or (latest_revision_number is None):
        return release_directory_base(release)
    return release_directory_base(release) / latest_revision_number


def release_directory_base(release: sql.Release) -> pathlib.Path:
    """Determine the filesystem directory for a given release based on its phase."""
    phase = release.phase
    project_name = release.project.name
    version_name = release.version

    base_dir: pathlib.Path | None = None
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
    return base_dir / project_name / version_name


def release_directory_revision(release: sql.Release) -> pathlib.Path | None:
    """Return the path to the directory containing the active files for a given release phase."""
    path_project = release.project.name
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


def release_directory_version(release: sql.Release) -> pathlib.Path:
    """Return the path to the directory containing the active files for a given release phase."""
    path_project = release.project.name
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
