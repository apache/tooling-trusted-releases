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

import sqlmodel

import atr.catalog_site as catalog_site
import atr.db as db
import atr.models.args as args
import atr.models.results as results
import atr.models.sql as sql
import atr.tasks.checks as checks
import atr.tasks.task as task


@checks.with_model(args.CatalogSiteGenerate)
async def generate(task_args: args.CatalogSiteGenerate, *, task_id: int | None = None) -> results.Results | None:
    """Regenerate the static release catalog site.

    A null project_key rebuilds the whole site (bootstrap and drift repair); a
    set project_key rewrites just that project's subtree in response to a
    catalogue change.
    """
    async with db.session() as data:
        # A full rebuild rewrites the whole tree and a partial rewrites the global index
        # pages besides its own subtree, so an earlier regen that overlaps this one would
        # race its writes. Let it finish and come back to this. Two different projects
        # don't overlap, so they're free to run at once.
        if (task_id is not None) and await _older_regen_active(data, task_id, task_args.project_key):
            raise task.DeferredError
        if task_args.project_key is None:
            await catalog_site.generate_all(data)
        else:
            await catalog_site.regenerate_project(data, task_args.project_key)
    return None


async def _older_regen_active(data: db.Session, task_id: int, project_key: str | None) -> bool:
    # True when an earlier-queued (lower id) regeneration that overlaps this one is still
    # ACTIVE. A full regen (null project) touches every page, so it conflicts with any
    # other; a project regen conflicts only with a full regen or another of its own project.
    via = sql.validate_instrumented_attribute
    conditions = [
        via(sql.Task.task_type) == sql.TaskType.CATALOG_SITE_GENERATE,
        via(sql.Task.status) == sql.TaskStatus.ACTIVE,
        via(sql.Task.id) < task_id,
    ]
    if project_key is not None:
        conditions.append(
            sqlmodel.or_(
                via(sql.Task.project_key).is_(None),
                via(sql.Task.project_key) == project_key,
            )
        )
    stmt = sqlmodel.select(via(sql.Task.id)).where(*conditions).limit(1)
    return (await data.execute(stmt)).first() is not None
