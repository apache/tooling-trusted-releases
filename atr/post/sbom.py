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

import atr.analysis as analysis
import atr.blueprints.post as post
import atr.get as get
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web


@post.typed
async def components(
    session: web.Committer,
    _sbom_components: Literal["sbom/components"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_path: safe.RelPath,
    _sbom_form: shared.sbom.ScanSBOMForm,
) -> web.WerkzeugResponse:
    """
    URL: /sbom/components/<project_key>/<version_key>/<path:file_path>
    """
    # The page is keyed on the file the SBOM describes, so find the SBOM before scanning it
    release = await shared.sbom.release_in_phase(session, project_key, version_key)
    sbom_path = await shared.sbom.sbom_for_artifact(paths.release_directory(release), file_path)
    if sbom_path is None:
        raise base.ASFQuartException("This file has no CycloneDX JSON SBOM", errorcode=404)
    return await _scan(session, release, project_key, version_key, sbom_path, file_path)


@post.typed
async def quality(
    session: web.Committer,
    _sbom_quality: Literal["sbom/quality"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_path: safe.RelPath,
    _sbom_form: shared.sbom.AugmentSBOMForm,
) -> web.WerkzeugResponse:
    """
    URL: /sbom/quality/<project_key>/<version_key>/<path:file_path>
    """
    return await _augment(session, project_key, version_key, file_path)


async def _augment(
    session: web.Committer, project_key: safe.ProjectKey, version_key: safe.VersionKey, rel_path: safe.RelPath
) -> web.WerkzeugResponse:
    """Augment a CycloneDX SBOM file."""
    # Check that the file is a .cdx.json archive before creating a revision
    file_name = rel_path.as_path().name
    if not analysis.is_cyclonedx_json(file_name):
        raise base.ASFQuartException("SBOM augmentation is only supported for CycloneDX JSON files", errorcode=400)

    try:
        release = await session.release(project_key, version_key, with_committee=False, with_project=False)
        revision_number = release.safe_latest_revision_number
        log.info(f"Augmenting SBOM for {project_key} {version_key} {revision_number!s} {rel_path!s}")
        async with storage.write_as_project_committee_member(project_key) as wacm:
            sbom_task = await wacm.sbom.augment_cyclonedx(
                project_key,
                version_key,
                revision_number,
                rel_path,
            )

    except Exception as e:
        log.exception("Error augmenting SBOM:")
        await quart.flash(f"Error augmenting SBOM: {e!s}", "error")
        return await session.redirect(
            get.sbom.quality,
            project_key=str(project_key),
            version_key=str(version_key),
            file_path=str(rel_path),
        )

    return await session.redirect(
        get.sbom.quality,
        success=f"SBOM augmentation task queued for {file_name} (task ID: {util.unwrap(sbom_task.id)})",
        project_key=str(project_key),
        version_key=str(version_key),
        file_path=str(rel_path),
    )


async def _scan(
    session: web.Committer,
    release: sql.Release,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    rel_path: safe.RelPath,
    artifact_path: safe.RelPath,
) -> web.WerkzeugResponse:
    """Scan a CycloneDX SBOM file for vulnerabilities using OSV, returning to the artifact's page."""
    if not analysis.is_cyclonedx_json(rel_path.as_path().name):
        raise base.ASFQuartException("OSV scanning is only supported for CycloneDX JSON files", errorcode=400)

    try:
        revision_number = release.safe_latest_revision_number
        log.info(f"Starting OSV scan for {project_key!s} {version_key!s} {revision_number!s} {rel_path!s}")
        async with storage.write_as_project_committee_member(project_key) as wacm:
            sbom_task = await wacm.sbom.osv_scan_cyclonedx(
                project_key,
                version_key,
                revision_number,
                rel_path,
            )

    except Exception as e:
        log.exception("Error starting OSV scan:")
        await quart.flash(f"Error starting OSV scan: {e!s}", "error")
        return await session.redirect(
            get.sbom.components,
            project_key=str(project_key),
            version_key=str(version_key),
            file_path=str(artifact_path),
        )

    return await session.redirect(
        get.sbom.components,
        success=f"OSV vulnerability scan queued for {rel_path!s} (task ID: {util.unwrap(sbom_task.id)})",
        project_key=str(project_key),
        version_key=str(version_key),
        file_path=str(artifact_path),
    )
