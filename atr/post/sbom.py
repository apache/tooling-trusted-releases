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

from typing import TYPE_CHECKING

import asfquart.base as base
import quart

import atr.blueprints.post as post
import atr.db as db
import atr.form as form
import atr.get as get
import atr.log as log
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web

if TYPE_CHECKING:
    import pathlib


@post.committer("/sbom/report/<project>/<version>/<path:file_path>")
@post.form(shared.sbom.SBOMForm)
async def report(
    session: web.Committer, sbom_form: shared.sbom.SBOMForm, project: str, version: str, file_path: str
) -> web.WerkzeugResponse:
    await session.check_access(project)

    validated_path = form.to_relpath(file_path)
    if validated_path is None:
        raise base.ASFQuartException("Invalid file path", errorcode=400)

    match sbom_form:
        case shared.sbom.AugmentSBOMForm():
            return await _augment(session, project, version, validated_path)

        case shared.sbom.ScanSBOMForm():
            return await _scan(session, project, version, validated_path)


async def _augment(
    session: web.Committer, project_name: str, version_name: str, rel_path: pathlib.Path
) -> web.WerkzeugResponse:
    """Augment a CycloneDX SBOM file."""
    # Check that the file is a .cdx.json archive before creating a revision
    if not (rel_path.name.endswith(".cdx.json")):
        raise base.ASFQuartException("SBOM augmentation is only supported for .cdx.json files", errorcode=400)

    try:
        async with db.session() as data:
            release = await data.release(project_name=project_name, version=version_name).demand(
                RuntimeError("Release does not exist for new revision creation")
            )
            revision_number = release.latest_revision_number
            if revision_number is None:
                raise RuntimeError("No revision number found for new revision creation")
            log.info(f"Augmenting SBOM for {project_name} {version_name} {revision_number} {rel_path}")
        async with storage.write_as_project_committee_member(project_name) as wacm:
            sbom_task = await wacm.sbom.augment_cyclonedx(
                project_name,
                version_name,
                revision_number,
                rel_path,
            )

    except Exception as e:
        log.exception("Error augmenting SBOM:")
        await quart.flash(f"Error augmenting SBOM: {e!s}", "error")
        return await session.redirect(
            get.sbom.report,
            project_name=project_name,
            version_name=version_name,
            file_path=str(rel_path),
        )

    return await session.redirect(
        get.sbom.report,
        success=f"SBOM augmentation task queued for {rel_path.name} (task ID: {util.unwrap(sbom_task.id)})",
        project_name=project_name,
        version_name=version_name,
        file_path=str(rel_path),
    )


async def _scan(
    session: web.Committer, project_name: str, version_name: str, rel_path: pathlib.Path
) -> web.WerkzeugResponse:
    """Scan a CycloneDX SBOM file for vulnerabilities using OSV."""
    if not (rel_path.name.endswith(".cdx.json")):
        raise base.ASFQuartException("OSV scanning is only supported for .cdx.json files", errorcode=400)

    try:
        async with db.session() as data:
            release = await data.release(project_name=project_name, version=version_name).demand(
                RuntimeError("Release does not exist for OSV scan")
            )
            revision_number = release.latest_revision_number
            if revision_number is None:
                raise RuntimeError("No revision number found for OSV scan")
            log.info(f"Starting OSV scan for {project_name} {version_name} {revision_number} {rel_path}")
        async with storage.write_as_project_committee_member(project_name) as wacm:
            sbom_task = await wacm.sbom.osv_scan_cyclonedx(
                project_name,
                version_name,
                revision_number,
                rel_path,
            )

    except Exception as e:
        log.exception("Error starting OSV scan:")
        await quart.flash(f"Error starting OSV scan: {e!s}", "error")
        return await session.redirect(
            get.sbom.report,
            project_name=project_name,
            version_name=version_name,
            file_path=str(rel_path),
        )

    return await session.redirect(
        get.sbom.report,
        success=f"OSV vulnerability scan queued for {rel_path.name} (task ID: {util.unwrap(sbom_task.id)})",
        project_name=project_name,
        version_name=version_name,
        file_path=str(rel_path),
    )
