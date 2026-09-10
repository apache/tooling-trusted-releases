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
import datetime
from typing import Any, Final

import sqlmodel

import atr.catalog_site as catalog_site
import atr.constants as constants
import atr.db as db
import atr.log as log
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.sbom.heatmap
import atr.shared.catalog as catalog
import atr.tasks.checks as checks

_REFRESH_INTERVAL: Final = datetime.timedelta(days=7)
_RETRY_INTERVAL: Final = datetime.timedelta(days=1)


@checks.with_model(args.SBOMHeatmap)
async def analyse(task_args: args.SBOMHeatmap) -> results.SBOMHeatmap:
    async with db.session() as data:
        release = await data.release(
            project_key=str(task_args.project_key),
            version=str(task_args.version_key),
            phase=sql.ReleasePhase.RELEASE,
        ).demand(ValueError("Heatmap analysis requires a published release"))
        artifacts = await data.artifact(project_key=release.project_key, version=release.version, _release=True).all()
        versions = catalog.assemble(
            release.project.version_method, artifacts, [], datetime.datetime.now(datetime.UTC)
        ).versions
        sboms = {path: url for version in versions for path, url in catalog.sbom_urls(version).items()}
    if not sboms:
        raise ValueError("Release has no published SBOMs")
    return await atr.sbom.heatmap.analyse(sboms)


async def complete(data: db.Session, task_id: int, task_args: dict[str, Any]) -> None:
    identity = args.SBOMHeatmap.model_validate(task_args)
    via = sql.validate_instrumented_attribute
    await data.execute(
        sqlmodel.delete(sql.Task).where(
            via(sql.Task.task_type) == sql.TaskType.SBOM_HEATMAP,
            via(sql.Task.project_key) == str(identity.project_key),
            via(sql.Task.version_key) == str(identity.version_key),
            via(sql.Task.status) == sql.TaskStatus.COMPLETED,
            via(sql.Task.id) != task_id,
        )
    )
    await catalog_site.queue_regeneration(data, constants.SYSTEM_SERVICE_UID, str(identity.project_key))


async def queue(data: db.Session, project_key: safe.ProjectKey, version_key: safe.VersionKey) -> sql.Task:
    existing = await data.task(
        task_type=sql.TaskType.SBOM_HEATMAP,
        project_key=str(project_key),
        version_key=str(version_key),
        status_in=[sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE],
    ).get()
    if existing is not None:
        return existing
    task = sql.Task(
        task_type=sql.TaskType.SBOM_HEATMAP,
        task_args=args.SBOMHeatmap(project_key=project_key, version_key=version_key).model_dump(),
        asf_uid=constants.SYSTEM_SERVICE_UID,
        project_key=str(project_key),
        version_key=str(version_key),
    )
    data.add(task)
    return task


async def sweep() -> None:
    now = datetime.datetime.now(datetime.UTC)
    via = sql.validate_instrumented_attribute
    with_sboms = (
        sqlmodel.select(sql.Release.key)
        .join(
            sql.Artifact,
            sqlmodel.and_(
                sql.Artifact.project_key == sql.Release.project_key, sql.Artifact.version == sql.Release.version
            ),
        )
        .where(sql.Release.phase == sql.ReleasePhase.RELEASE, via(sql.Artifact.sbom_path).is_not(None))
        .where(via(sql.Release.is_archived).is_(False), sql.Artifact.sbom_path != "")
    )
    with_reports = (
        sqlmodel.select(sql.Release.key)
        .join(
            sql.Task,
            sqlmodel.and_(sql.Task.project_key == sql.Release.project_key, sql.Task.version_key == sql.Release.version),
        )
        .where(sql.Task.task_type == sql.TaskType.SBOM_HEATMAP, sql.Task.status == sql.TaskStatus.COMPLETED)
    )
    async with db.session() as data:
        release_keys = (await data.execute(with_sboms.union(with_reports))).scalars().all()
    projects = set()
    for release_key in release_keys:
        try:
            project_key = await _sweep_release(release_key, now)
            if project_key is not None:
                projects.add(project_key)
        except Exception:
            log.exception(f"Heatmap sweep failed for {release_key}")
    async with db.session() as data:
        await data.begin_immediate()
        for project_key in sorted(projects):
            await catalog_site.queue_regeneration(data, constants.SYSTEM_SERVICE_UID, project_key)
        await data.commit()
    log.info(f"Heatmap sweep checked {len(release_keys)} releases and requested {len(projects)} project regenerations")


def _analysis_due(
    latest: sql.Task | None, completed: sql.Task | None, applicable: bool, now: datetime.datetime
) -> bool:
    if latest is not None:
        if latest.status in {sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE}:
            return False
        if (
            (latest.status in {sql.TaskStatus.FAILED, sql.TaskStatus.BROKEN})
            and (latest.completed is not None)
            and (latest.completed > (now - _RETRY_INTERVAL))
        ):
            return False
    return (
        (not applicable)
        or (completed is None)
        or (completed.completed is None)
        or (completed.completed <= (now - _REFRESH_INTERVAL))
    )


def _publication_needed(project_key: str, version_key: str, completed: datetime.datetime | None) -> bool:
    directory = paths.get_catalog_site_dir() / project_key / version_key
    if completed is None:
        return any((directory / name).path.exists() for name in ("heatmap.json", "heatmap.html"))
    for name in ("heatmap.json", "heatmap.html", "index.html"):
        try:
            if (directory / name).path.stat().st_mtime < completed.timestamp():
                return True
        except FileNotFoundError:
            return True
    return False


async def _sweep_release(release_key: str, now: datetime.datetime) -> str | None:
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        await data.begin_immediate()
        release = await data.release(key=release_key).get()
        if release is None:
            return None
        artifacts = await data.artifact(project_key=release.project_key, version=release.version, _release=True).all()
        versions = catalog.assemble(release.project.version_method, artifacts, [], now).versions
        version = next(iter(versions), None)
        history = data.task(
            task_type=sql.TaskType.SBOM_HEATMAP, project_key=release.project_key, version_key=release.version
        )
        latest = await history.order_by(via(sql.Task.added).desc(), via(sql.Task.id).desc()).limit(1).get()
        completed = latest
        if (completed is not None) and (completed.status != sql.TaskStatus.COMPLETED):
            completed = (
                await data.task(
                    task_type=sql.TaskType.SBOM_HEATMAP,
                    project_key=release.project_key,
                    version_key=release.version,
                    status=sql.TaskStatus.COMPLETED,
                )
                .order_by(via(sql.Task.completed).desc(), via(sql.Task.id).desc())
                .limit(1)
                .get()
            )
        snapshot = completed.result if completed and isinstance(completed.result, results.SBOMHeatmap) else None
        applicable = (
            (release.phase == sql.ReleasePhase.RELEASE)
            and (version is not None)
            and (catalog_site.applicable_heatmap(snapshot, version) is not None)
        )
        if (
            (release.phase == sql.ReleasePhase.RELEASE)
            and (not release.is_archived)
            and (version is not None)
            and catalog.sbom_urls(version)
            and _analysis_due(latest, completed, applicable, now)
        ):
            await queue(data, release.safe_project_key, release.safe_version_key)
        project_key, version_key = release.project_key, release.version
        published_at = completed.completed if applicable and completed else None
        await data.commit()
    if await asyncio.to_thread(_publication_needed, project_key, version_key, published_at):
        return project_key
    return None
