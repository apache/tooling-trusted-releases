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

from __future__ import annotations

from typing import Literal

import asfquart.base as base
import quart

import atr.blueprints.post as post
import atr.db as db
import atr.get as get
import atr.log as log
import atr.models.safe as safe
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web


@post.typed
async def report(
    session: web.Committer,
    _sbom_report: Literal["sbom/report"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_path: safe.RelPath,
    sbom_form: shared.sbom.SBOMForm,
) -> web.WerkzeugResponse:
    """
    URL: /sbom/report/<project_key>/<version_key>/<path:file_path>
    """

    match sbom_form:
        case shared.sbom.AugmentSBOMForm():
            return await _augment(session, project_key, version_key, file_path)

        case shared.sbom.ScanSBOMForm():
            return await _scan(session, project_key, version_key, file_path)


async def _augment(
    session: web.Committer, project_key: safe.ProjectKey, version_key: safe.VersionKey, rel_path: safe.RelPath
) -> web.WerkzeugResponse:
    """Augment a CycloneDX SBOM file."""
    path = rel_path.as_path()
    # Check that the file is a .cdx.json archive before creating a revision
    if not (path.name.endswith(".cdx.json")):
        raise base.ASFQuartException("SBOM augmentation is only supported for .cdx.json files", errorcode=400)

    try:
        async with db.session() as data:
            release = await data.release(project_key=str(project_key), version=str(version_key)).demand(
                RuntimeError("Release does not exist for new revision creation")
            )
            revision_number = release.latest_revision_number
            if revision_number is None:
                raise RuntimeError("No revision number found for new revision creation")
            log.info(f"Augmenting SBOM for {project_key} {version_key} {revision_number} {path}")
        async with storage.write_as_project_committee_member(project_key) as wacm:
            sbom_task = await wacm.sbom.augment_cyclonedx(
                project_key,
                version_key,
                revision_number,
                path,
            )

    except Exception as e:
        log.exception("Error augmenting SBOM:")
        await quart.flash(f"Error augmenting SBOM: {e!s}", "error")
        return await session.redirect(
            get.sbom.report,
            project_key=project_key,
            version_key=version_key,
            file_path=str(rel_path),
        )

    return await session.redirect(
        get.sbom.report,
        success=f"SBOM augmentation task queued for {path.name} (task ID: {util.unwrap(sbom_task.id)})",
        project_key=project_key,
        version_key=version_key,
        file_path=str(rel_path),
    )


async def _scan(
    session: web.Committer, project_key: safe.ProjectKey, version_key: safe.VersionKey, rel_path: safe.RelPath
) -> web.WerkzeugResponse:
    """Scan a CycloneDX SBOM file for vulnerabilities using OSV."""
    path = rel_path.as_path()
    if not (path.name.endswith(".cdx.json")):
        raise base.ASFQuartException("OSV scanning is only supported for .cdx.json files", errorcode=400)

    try:
        async with db.session() as data:
            release = await data.release(project_key=str(project_key), version=str(version_key)).demand(
                RuntimeError("Release does not exist for OSV scan")
            )
            revision_number = release.latest_revision_number
            if revision_number is None:
                raise RuntimeError("No revision number found for OSV scan")
            log.info(f"Starting OSV scan for {project_key} {version_key} {revision_number} {path}")
        async with storage.write_as_project_committee_member(project_key) as wacm:
            sbom_task = await wacm.sbom.osv_scan_cyclonedx(
                project_key,
                version_key,
                revision_number,
                path,
            )

    except Exception as e:
        log.exception("Error starting OSV scan:")
        await quart.flash(f"Error starting OSV scan: {e!s}", "error")
        return await session.redirect(
            get.sbom.report,
            project_key=project_key,
            version_key=version_key,
            file_path=str(rel_path),
        )

    return await session.redirect(
        get.sbom.report,
        success=f"OSV vulnerability scan queued for {path.name} (task ID: {util.unwrap(sbom_task.id)})",
        project_key=project_key,
        version_key=version_key,
        file_path=str(rel_path),
    )
