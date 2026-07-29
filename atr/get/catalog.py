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
from typing import Literal

import asfquart.base as base

import atr.api as api
import atr.blueprints.get as get
import atr.db as db
import atr.models.safe as safe
import atr.shared.catalog as catalog
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def project(_session: web.Public, _catalog: Literal["catalog"], project_key: safe.ProjectKey) -> str:
    """
    URL: /catalog/<project_key>
    The release catalogue for a project: versions and their artifacts.
    """
    async with db.session() as data:
        project_obj = await data.project(key=str(project_key)).demand(
            base.ASFQuartException(f"Project {project_key} not found", errorcode=404)
        )
        artifacts = await data.artifact(project_key=project_obj.key, _release=True).all()
        project_cycles = await data.project_cycle(project_key=project_obj.key).all()

    now = datetime.datetime.now(datetime.UTC)
    assembled = catalog.assemble(
        project_obj.version_method,
        artifacts,
        project_cycles,
        now,
    )

    return await template.render(
        "catalog.html",
        project=project_obj,
        versions=assembled.versions,
        groups=catalog.cycle_groups(assembled.versions, project_cycles, now) if assembled.grouped else [],
        grouped=assembled.grouped,
        api=api,
        format_datetime=util.format_datetime,
    )
