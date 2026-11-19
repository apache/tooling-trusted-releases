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

import json
from typing import Literal

import aiofiles
import aiofiles.os

import atr.attestable as attestable
import atr.blueprints.get as get
import atr.db as db
import atr.errors as errors
import atr.hashes as hashes
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.web as web


@get.typed
async def data(
    _session: web.Public,
    _result_data: Literal["result/data"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    check_id: int,
) -> web.TextResponse:
    """
    URL: /result/data/<project_key>/<version_key>/<check_id>
    Show a check result as formatted JSON.
    """
    async with db.session() as data:
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
        ).demand(errors.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404))

        if release.committee is None:
            raise errors.ASFQuartException(errors.RELEASE_NO_COMMITTEE, errorcode=500)

        check_result = await data.check_result(
            id=check_id,
            release_key=release.key,
        ).demand(errors.ASFQuartException("Check result not found", errorcode=404))

    payload = check_result.model_dump(mode="json", exclude={"release"})
    body = json.dumps(payload, indent=2, sort_keys=True)
    # audit_guidance TextResponse is being used intentionally because browsers display JSON well
    return web.TextResponse(f"{body}\n")


@get.typed
async def excludes(
    _session: web.Public,
    _result_excludes: Literal["result/excludes"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    check_id: int,
) -> web.TextResponse:
    async with db.session() as data:
        release = await data.release(project_key=str(project_key), version=str(version_key)).demand(
            errors.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404)
        )
        check_result = await data.check_result(id=check_id, release_key=release.key).demand(
            errors.ASFQuartException("Check result not found", errorcode=404)
        )
    return web.TextResponse(await _excludes_text(project_key, version_key, check_result))


async def _excludes_text(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, check_result: sql.CheckResult
) -> str:
    source = (check_result.data or {}).get("excludes_source")
    revision = safe.RevisionNumber(str(check_result.revision_number))
    rel_path = str(check_result.primary_rel_path)
    snapshot = await attestable.load(project_key, version_key, revision)
    if (source == "policy") and (snapshot is not None):
        return "\n".join(snapshot.policy.get("source_excludes_rat") or []) + "\n"
    if (source == "url") and (
        record := (await attestable.load_rat_excludes(project_key, version_key, revision)).get(rel_path)
    ):
        return f"{record.url}\n\n{record.content or record.error}\n"
    if (source == "archive") and (snapshot is not None) and (content_hash := attestable.path_hash(snapshot, rel_path)):
        archive_key = hashes.filesystem_archives_key(content_hash)
        archive_dir = paths.get_archives_dir() / str(project_key) / str(version_key) / archive_key
        excludes_path = archive_dir.path / check_result.data.get("directory", ".") / ".rat-excludes"
        if await aiofiles.os.path.isfile(excludes_path):
            async with aiofiles.open(excludes_path, encoding="utf-8", errors="replace") as f:
                return await f.read()
    return "No exclusions file is available for this check result\n"
