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

import asyncio
import csv
import datetime
import io
import pathlib
import re
import shutil
import time
import uuid
from collections.abc import Iterable
from typing import Final

import aiofiles.os
import quart.datastructures as datastructures
import sqlmodel

import atr.config as config
import atr.db as db
import atr.models.sql as sql
import atr.shared.catalogue_diff as catalogue_diff

# CSV columns per table, matching the fields the catalogue_rows models parse, so a downloaded
# file re-uploads cleanly
_EXPORT_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "projects": ("key", "name", "status", "committee_key", "version_method"),
    "releases": ("key", "project_key", "version", "phase", "created", "released", "archived", "release_status"),
    "artifacts": (
        "project_key",
        "version",
        "artifact_path",
        "signature_path",
        "checksum_path",
        "sbom_path",
        "classification",
        "download_path_suffix",
        "managed",
        "dated",
    ),
}

# The uploaded CSVs live here, one directory per token
IMPORT_ROOT: Final[pathlib.Path] = pathlib.Path(config.get().STATE_DIR) / "temporary" / "catalogue-imports"
_TABLES: Final[tuple[str, ...]] = ("projects", "releases", "artifacts")
# A token is a uuid4 hex string (no path separators or dots)
_TOKEN_RE: Final = re.compile(r"^[0-9a-f]{32}$")
_UPLOAD_TTL_SECONDS: Final = 24 * 60 * 60
# Stored beside the uploads rather than carried through the form, so the intent recorded at
# upload cannot differ from the one the preview showed
_MODE_MARKER: Final = "mode"
_COMMITTEE_MARKER: Final = "committee"
_FINGERPRINT_MARKER: Final = "fingerprint"


class StaleError(Exception):
    # The catalogue changed between the preview and the apply, so the diff the admin approved is not
    # the diff that would be written
    pass


async def build_snapshot(data: db.Session, committee_key: str) -> catalogue_diff.DbSnapshot:
    via = sql.validate_instrumented_attribute
    # Projects and the managed set are foundation-wide; releases and artifacts are scoped to
    # the workspace committee
    all_projects = (await data.execute(sqlmodel.select(via(sql.Project.key), via(sql.Project.committee_key)))).all()
    project_committee = {row[0]: row[1] for row in all_projects if row[1] is not None}
    workspace_keys = [key for key, committee in project_committee.items() if committee == committee_key]
    release_project = await _release_project(data, workspace_keys)
    artifact_by_dist, artifact_pks = await _artifacts(data, workspace_keys)
    release_keys = (await data.execute(sqlmodel.select(via(sql.Release.key)))).scalars().all()
    return catalogue_diff.DbSnapshot(
        project_committee=project_committee,
        release_project=release_project,
        artifact_by_dist=artifact_by_dist,
        managed_project_keys=await _managed_project_keys(data),
        release_keys=frozenset(release_keys),
        artifact_pks=artifact_pks,
    )


def discard(token: str) -> None:
    target = _token_dir(token)
    if target.is_dir():
        shutil.rmtree(target)


async def export_table(data: db.Session, committee_key: str, table: str) -> str:
    # The import refuses to touch a project with live workflow data, so the export leaves it out.
    # Otherwise a downloaded file could not be uploaded again without editing those rows back out
    managed = await _managed_project_keys(data)
    projects = [project for project in await _committee_projects(data, committee_key) if project.key not in managed]
    if table == "projects":
        return _to_csv(table, (_project_export(project) for project in projects))
    project_keys = [project.key for project in projects]
    if table == "releases":
        releases = await _committee_releases(data, project_keys)
        return _to_csv(table, (_release_export(release) for release in releases))
    if table == "artifacts":
        artifacts = await _committee_artifacts(data, project_keys)
        return _to_csv(table, (_artifact_export(artifact) for artifact in artifacts))
    raise KeyError(table)


def mode_of(token: str) -> catalogue_diff.Mode:
    marker = _token_dir(token) / _MODE_MARKER
    if not marker.is_file():
        raise KeyError(token)
    return catalogue_diff.Mode(marker.read_text())


def committee_of(token: str) -> str:
    # The upload belongs to the committee it was previewed against, so it cannot be applied to another
    marker = _token_dir(token) / _COMMITTEE_MARKER
    if not marker.is_file():
        raise KeyError(token)
    return marker.read_text()


def fingerprint_of(token: str) -> str:
    marker = _token_dir(token) / _FINGERPRINT_MARKER
    if not marker.is_file():
        raise KeyError(token)
    return marker.read_text()


def record_fingerprint(token: str, value: str) -> None:
    target = _token_dir(token)
    if not target.is_dir():
        raise KeyError(token)
    (target / _FINGERPRINT_MARKER).write_text(value)


def read_uploads(token: str) -> dict[str, list[dict[str, str]]]:
    target = _token_dir(token)
    if not target.is_dir():
        raise KeyError(token)
    rows: dict[str, list[dict[str, str]]] = {}
    for name in _TABLES:
        path = target / f"{name}.csv"
        if path.is_file():
            with path.open(newline="") as stream:
                rows[name] = list(csv.DictReader(stream))
    return rows


async def store_uploads(
    files: dict[str, datastructures.FileStorage], committee_key: str, mode: catalogue_diff.Mode
) -> str:
    await asyncio.to_thread(_sweep_stale)
    token = uuid.uuid4().hex
    target = _token_dir(token)
    await aiofiles.os.makedirs(target, exist_ok=True)
    for name, upload in files.items():
        if name in _TABLES:
            await upload.save(target / f"{name}.csv")
    await asyncio.to_thread((target / _COMMITTEE_MARKER).write_text, committee_key)
    await asyncio.to_thread((target / _MODE_MARKER).write_text, str(mode))
    return token


def _artifact_export(artifact: sql.Artifact) -> dict[str, str]:
    return {
        "project_key": artifact.project_key,
        "version": artifact.version,
        "artifact_path": artifact.artifact_path,
        "signature_path": artifact.signature_path or "",
        "checksum_path": artifact.checksum_path or "",
        "sbom_path": artifact.sbom_path or "",
        "classification": artifact.classification or "",
        "download_path_suffix": artifact.download_path_suffix or "",
        "managed": "true" if artifact.managed else "false",
        "dated": _date_str(artifact.dated),
    }


async def _artifacts(
    data: db.Session, workspace_keys: list[str]
) -> tuple[dict[tuple[str, str], tuple[str, str, str]], frozenset[tuple[str, str, str]]]:
    # An artifact is identified by its dist path, but only artifacts that have one can be matched
    # that way; the primary keys cover every artifact, including any with no dist path
    if not workspace_keys:
        return {}, frozenset()
    via = sql.validate_instrumented_attribute
    rows = (
        await data.execute(
            sqlmodel.select(
                via(sql.Artifact.project_key),
                via(sql.Artifact.version),
                via(sql.Artifact.artifact_path),
                via(sql.Artifact.download_path_suffix),
            ).where(via(sql.Artifact.project_key).in_(workspace_keys))
        )
    ).all()
    by_dist = {(row[3], row[2]): (row[0], row[1], row[2]) for row in rows if row[3]}
    pks = frozenset((row[0], row[1], row[2]) for row in rows)
    return by_dist, pks


async def _committee_artifacts(data: db.Session, project_keys: list[str]) -> list[sql.Artifact]:
    if not project_keys:
        return []
    via = sql.validate_instrumented_attribute
    result = await data.execute(sqlmodel.select(sql.Artifact).where(via(sql.Artifact.project_key).in_(project_keys)))
    return list(result.scalars().all())


async def _committee_projects(data: db.Session, committee_key: str) -> list[sql.Project]:
    via = sql.validate_instrumented_attribute
    result = await data.execute(sqlmodel.select(sql.Project).where(via(sql.Project.committee_key) == committee_key))
    return list(result.scalars().all())


async def _committee_releases(data: db.Session, project_keys: list[str]) -> list[sql.Release]:
    if not project_keys:
        return []
    via = sql.validate_instrumented_attribute
    result = await data.execute(sqlmodel.select(sql.Release).where(via(sql.Release.project_key).in_(project_keys)))
    return list(result.scalars().all())


async def _managed_project_keys(data: db.Session) -> frozenset[str]:
    # A project is managed once one of its releases has a revision, which is where ATR keeps the
    # files it holds. Foundation-wide, since a row can name a project in any committee
    via = sql.validate_instrumented_attribute
    rows = (
        await data.execute(
            sqlmodel.select(via(sql.Release.project_key))
            .join(sql.Revision, via(sql.Revision.release_key) == via(sql.Release.key))
            .distinct()
        )
    ).all()
    return frozenset(row[0] for row in rows)


def _date_str(value: datetime.datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _project_export(project: sql.Project) -> dict[str, str]:
    return {
        "key": project.key,
        "name": project.name or "",
        "status": str(project.status),
        "committee_key": project.committee_key or "",
        "version_method": str(project.version_method),
    }


def _release_export(release: sql.Release) -> dict[str, str]:
    return {
        "key": release.key,
        "project_key": release.project_key,
        "version": release.version,
        "phase": str(release.phase),
        "created": _date_str(release.created),
        "released": _date_str(release.released),
        "archived": _date_str(release.archived),
        "release_status": "retired" if release.is_archived else "active",
    }


async def _release_project(data: db.Session, workspace_keys: list[str]) -> dict[str, str]:
    if not workspace_keys:
        return {}
    via = sql.validate_instrumented_attribute
    rows = (
        await data.execute(
            sqlmodel.select(via(sql.Release.key), via(sql.Release.project_key)).where(
                via(sql.Release.project_key).in_(workspace_keys)
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def _sweep_stale() -> None:
    if not IMPORT_ROOT.is_dir():
        return
    cutoff = time.time() - _UPLOAD_TTL_SECONDS
    for entry in IMPORT_ROOT.iterdir():
        if entry.is_dir() and (entry.stat().st_mtime < cutoff):
            shutil.rmtree(entry, ignore_errors=True)


def _to_csv(table: str, rows: Iterable[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(_EXPORT_COLUMNS[table]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _token_dir(token: str) -> pathlib.Path:
    if not _TOKEN_RE.fullmatch(token):
        raise KeyError(token)
    return IMPORT_ROOT / token
