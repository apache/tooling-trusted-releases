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

from typing import Literal

import aiofiles.os
import asfquart.base as base

import atr.analysis as analysis
import atr.form as form
import atr.models.safe as safe
import atr.models.sql as sql
import atr.web as web

type AUGMENT = Literal["augment"]
type SCAN = Literal["scan"]


async def release_in_phase(
    session: web.Committer, project_key: safe.ProjectKey, version_key: safe.VersionKey, with_committee: bool = False
) -> sql.Release:
    # If the draft is not found, we try to get the release candidate
    try:
        return await session.release(project_key, version_key, with_committee=with_committee)
    except base.ASFQuartException:
        return await session.release(
            project_key, version_key, phase=sql.ReleasePhase.RELEASE_CANDIDATE, with_committee=with_committee
        )


async def sbom_for_artifact(base_path: safe.StatePath, file_path: safe.RelPath) -> safe.RelPath | None:
    # Only the JSON suffixes pair here, because the SBOM tooling reads JSON alone
    for candidate in analysis.sbom_candidates(str(file_path), analysis.CYCLONEDX_JSON_SUFFIXES):
        rel_path = safe.RelPath(candidate)
        if await aiofiles.os.path.isfile((base_path / rel_path).path):
            return rel_path
    return None


class AugmentSBOMForm(form.Empty):
    variant: AUGMENT = form.value(AUGMENT)


class ScanSBOMForm(form.Empty):
    variant: SCAN = form.value(SCAN)
