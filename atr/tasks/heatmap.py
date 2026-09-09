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

import datetime
from typing import Any

import sqlmodel

import atr.catalog_site as catalog_site
import atr.constants as constants
import atr.db as db
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.sbom.heatmap
import atr.shared.catalog as catalog
import atr.tasks.checks as checks


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
