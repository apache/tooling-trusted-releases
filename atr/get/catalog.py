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

import dataclasses
import datetime
from collections.abc import Sequence
from typing import Literal

import asfquart.base as base

import atr.api as api
import atr.blueprints.get as get
import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.shared.catalog as catalog
import atr.template as template
import atr.util as util
import atr.web as web


@dataclasses.dataclass
class CatalogQuery(web.PageQuery):
    limit: int = 200


@get.typed
async def project(
    _session: web.Public, _catalog: Literal["catalog"], project_key: safe.ProjectKey, query_args: CatalogQuery
) -> str:
    """
    URL: /catalog/<project_key>
    The release catalogue for a project: versions and their artifacts.
    """
    try:
        validation.pagination_args_validate(query_args)
    except ValueError as e:
        raise base.ASFQuartException(str(e), errorcode=400)
    async with db.session() as data:
        project_obj = await data.project(key=str(project_key)).demand(
            base.ASFQuartException(f"Project {project_key} not found", errorcode=404)
        )
        project_cycles = await data.project_cycle(project_key=project_obj.key).all()
        rows = await interaction.catalog_version_rows(data, project_obj.key)
        window = catalog.artifact_window(project_obj.version_method, rows, query_args.offset, query_args.limit)
        artifacts = await _page_artifacts(data, project_obj.key, window)

    now = datetime.datetime.now(datetime.UTC)
    assembled = catalog.assemble(project_obj.version_method, artifacts, project_cycles, now)
    versions = catalog.clip_versions(assembled.versions, window, query_args.limit)
    shown = sum(len(entry.artifacts) for entry in versions)
    page = web.page_nav(query_args.offset, query_args.limit, window.count, shown)
    groups = catalog.cycle_groups(versions, project_cycles, now, order=window.versions) if assembled.grouped else []

    return await template.render(
        "catalog.html",
        project=project_obj,
        committee=project_obj.committee,
        versions=versions,
        groups=groups,
        grouped=assembled.grouped,
        api=api,
        format_datetime=util.format_datetime,
        count=window.count,
        limit=query_args.limit,
        page=page,
        continued_version=window.versions[0] if window.skip else None,
    )


async def _page_artifacts(data: db.Session, project_key: str, window: catalog.ArtifactWindow) -> Sequence[sql.Artifact]:
    if not window.versions:
        return []
    return await data.artifact(project_key=project_key, version_in=window.versions, _release=True).all()
