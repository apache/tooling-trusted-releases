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

import aiofiles.os
import asfquart.base as base
import quart

import atr.analysis as analysis
import atr.blueprints.post as post
import atr.db.interaction as interaction
import atr.form as form
import atr.get as get
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.util as util
import atr.web as web


@post.typed
async def cache_reset(
    session: web.Committer,
    _draft_reset: Literal["draft/reset"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    _form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /draft/reset/<project_key>/<version_key>
    Start a new draft revision and switch this release to global caching.
    """
    if not session.is_admin:
        raise base.ASFQuartException("Admin access required", errorcode=403)

    description = "Empty revision to restart all checks without cache for the whole release candidate draft"
    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)
            result = await wacp.revision.create_revision_with_quarantine(
                project_key,
                version_key,
                session.uid,
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                description=description,
                reset_to_global_cache=True,
            )
    except datatypes.PhaseMismatchError as e:
        return await session.redirect(
            get.compose.selected,
            project_key=str(project_key),
            version_key=str(version_key),
            error=str(e),
        )

    success = "Release set back to global caching"
    if isinstance(result, sql.Quarantined):
        success += ". Archive validation in progress."
    return await session.redirect(
        get.compose.selected,
        project_key=str(project_key),
        version_key=str(version_key),
        success=success,
    )


@post.typed
async def delete(
    session: web.Committer,
    _draft_delete: Literal["draft/delete"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    _form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /draft/delete/<project_key>/<version_key>
    Delete a candidate draft and all its associated files.
    """
    # Delete the metadata from the database
    async with storage.write(session) as write:
        wacp = await write.as_project_committee_participant(project_key)
        error = await wacp.release.delete(
            project_key,
            version_key,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        )
        # Ensure that deletion errors are reported to the user
        if error is not None:
            await quart.flash(f"Error deleting candidate draft: {error}", "error")
            return await session.redirect(
                get.compose.selected, project_key=str(project_key), version_key=str(version_key)
            )

    return await session.redirect(get.root.index, success="Candidate draft deleted successfully")


@post.typed
async def delete_file(
    session: web.Committer,
    _draft_delete_file: Literal["draft/delete-file"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    delete_file_form: shared.draft.DeleteFileForm,
) -> web.WerkzeugResponse:
    """
    URL: /draft/delete-file/<project_key>/<version_key>
    Delete a specific file from the release candidate, creating a new revision.
    """
    rel_path_to_delete = delete_file_form.file_path
    path_to_delete = rel_path_to_delete.as_path()

    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)
            metadata_files_deleted = await wacp.release.delete_file(project_key, version_key, path_to_delete)
    except Exception as e:
        log.exception("Error deleting file:")
        await quart.flash(f"Error deleting file: {e!s}", "error")
        return await session.redirect(get.compose.selected, project_key=str(project_key), version_key=str(version_key))

    success_message = f"File '{path_to_delete.name}' deleted successfully"
    if metadata_files_deleted:
        success_message += f", and {util.plural(metadata_files_deleted, 'associated metadata file')} deleted"
    return await session.redirect(
        get.compose.selected, success=success_message, project_key=str(project_key), version_key=str(version_key)
    )


@post.typed
async def hashgen(
    session: web.Committer,
    _draft_hashgen: Literal["draft/hashgen"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_path: safe.RelPath,
    empty_form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /draft/hashgen/<project_key>/<version_key>/<file_path>
    Generate an sha512 hash file for a candidate draft file, creating a new revision.
    """
    rel_path = file_path.as_path()

    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)
            await wacp.release.generate_hash_file(project_key, version_key, rel_path)

    except Exception as e:
        log.exception("Error generating hash file:")
        await quart.flash(f"Error generating hash file: {e!s}", "error")
        return await session.redirect(get.compose.selected, project_key=str(project_key), version_key=str(version_key))

    return await session.redirect(
        get.compose.selected,
        success="SHA512 file generated successfully",
        project_key=str(project_key),
        version_key=str(version_key),
    )


@post.typed
async def quarantine_clear(
    session: web.Committer,
    _draft_quarantine_clear: Literal["draft/quarantine/clear"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    clear_form: shared.draft.ClearQuarantineForm,
) -> web.WerkzeugResponse:
    """URL: /draft/quarantine/clear/<project_key>/<version_key>"""
    async with storage.write(session) as write:
        wacp = await write.as_project_committee_participant(project_key)
        await wacp.revision.clear_quarantine(project_key, version_key, clear_form.quarantined_id)

    return await session.redirect(
        get.compose.selected,
        project_key=str(project_key),
        version_key=str(version_key),
        success="Quarantine failure dismissed",
    )


@post.typed
async def recheck(
    session: web.Committer,
    _draft_recheck: Literal["draft/recheck"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    empty_form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /draft/recheck/<project_key>/<version_key>
    Start a new draft revision and switch this release to release-local caching.
    """
    if not session.is_admin:
        raise base.ASFQuartException("Admin access required", errorcode=403)

    description = "Empty revision to restart all checks without cache for the whole release candidate draft"
    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)
            result = await wacp.revision.create_revision_with_quarantine(
                project_key,
                version_key,
                session.uid,
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                description=description,
                set_local_cache=True,
            )
    except datatypes.PhaseMismatchError as e:
        return await session.redirect(
            get.compose.selected,
            project_key=str(project_key),
            version_key=str(version_key),
            error=str(e),
        )

    success = "All checks restarted with release-local cache"
    if isinstance(result, sql.Quarantined):
        success += ". Archive validation in progress."
    return await session.redirect(
        get.compose.selected,
        project_key=str(project_key),
        version_key=str(version_key),
        success=success,
    )


@post.typed
async def sbomconvert(
    session: web.Committer,
    _draft_sbomconvert: Literal["draft/sbomconvert"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_path: safe.RelPath,
    empty_form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /draft/sbomconvert/<project_key>/<version_key>/<file_path>
    Convert an XML CycloneDX SBOM file into JSON, creating a new revision.
    """
    rel_path = file_path.as_path()

    # Check that the file is an XML SBOM before continuing
    if not analysis.is_cyclonedx_xml(rel_path.name):
        raise base.ASFQuartException(f"SBOM converter requires an XML SBOM. Received: {rel_path.name}", errorcode=400)

    try:
        description = "SBOM conversion through web interface"
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)

            async def modify(path: safe.StatePath, old_rev: sql.Revision | None) -> None:
                path_in_new_revision = path / rel_path
                # Swap only the trailing .xml, so .cdx.xml becomes .cdx.json rather than .cdx.cdx.json
                sbom_path_rel = rel_path.name.removesuffix(".xml") + ".json"
                sbom_path_in_new_revision = path / rel_path.with_name(sbom_path_rel)

                # Check that the source file exists in the new revision
                if not await aiofiles.os.path.exists(path_in_new_revision):
                    log.error(f"Source file {rel_path} not found in new revision for SBOM generation.")
                    raise web.FlashError("Source artifact file not found in the new revision.")

                # Check that the SBOM file does not already exist in the new revision
                if await aiofiles.os.path.exists(sbom_path_in_new_revision):
                    raise base.ASFQuartException("SBOM file already exists", errorcode=400)

                # This shouldn't happen as we need a revision to kick the task off from
                if old_rev is None:
                    raise web.FlashError("Internal error: Revision not found")

                # Create and queue the task, using paths within the new revision
                sbom_task = await wacp.sbom.convert_cyclonedx(
                    project_key,
                    version_key,
                    old_rev.safe_number,
                    path_in_new_revision,
                    sbom_path_in_new_revision,
                )
                success = await interaction.wait_for_task(sbom_task)
                if not success:
                    raise web.FlashError("Internal error: SBOM conversion timed out")

            result = await wacp.revision.create_revision_with_quarantine(
                project_key,
                version_key,
                session.uid,
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                description=description,
                modify=modify,
            )

    except Exception as e:
        log.exception("Error generating SBOM:")
        await quart.flash(f"Error generating SBOM: {e!s}", "error")
        return await session.redirect(get.compose.selected, project_key=str(project_key), version_key=str(version_key))

    success = f"SBOM generated for {rel_path.name}"
    if isinstance(result, sql.Quarantined):
        success += ". Archive validation in progress."
    return await session.redirect(
        get.compose.selected,
        success=success,
        project_key=str(project_key),
        version_key=str(version_key),
    )


@post.typed
async def sbomgen(
    session: web.Committer,
    _draft_sbomgen: Literal["draft/sbomgen"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_path: safe.RelPath,
    empty_form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /draft/sbomgen/<project_key>/<version_key>/<file_path>
    Generate a CycloneDX SBOM file for a candidate draft file, creating a new revision.
    """
    rel_path = file_path.as_path()

    # Check that the file is a .tar.gz archive before creating a revision
    if not (
        rel_path.name.endswith(".tar.gz")
        or rel_path.name.endswith(".tgz")
        or rel_path.name.endswith(".zip")
        or rel_path.name.endswith(".jar")
    ):
        raise base.ASFQuartException(
            f"SBOM generation requires .tar.gz, .tgz, .zip or .jar files. Received: {rel_path.name}", errorcode=400
        )

    try:
        description = "SBOM generation through web interface"
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)

            async def modify(path: safe.StatePath, old_rev: sql.Revision | None) -> None:
                path_in_new_revision = path / rel_path
                sbom_path_rel = rel_path.with_suffix(rel_path.suffix + ".cdx.json").name
                sbom_path_in_new_revision = path / rel_path.with_name(sbom_path_rel)

                # Check that the source file exists in the new revision
                if not await aiofiles.os.path.exists(path_in_new_revision):
                    log.error(f"Source file {rel_path} not found in new revision for SBOM generation.")
                    raise web.FlashError("Source artifact file not found in the new revision.")

                # Check that the SBOM file does not already exist in the new revision
                if await aiofiles.os.path.exists(sbom_path_in_new_revision):
                    raise base.ASFQuartException("SBOM file already exists", errorcode=400)

                # This shouldn't happen as we need a revision to kick the task off from
                if old_rev is None:
                    raise web.FlashError("Internal error: Revision not found")

                # Create and queue the task, using paths within the new revision
                sbom_task = await wacp.sbom.generate_cyclonedx(
                    project_key,
                    version_key,
                    old_rev.safe_number,
                    path_in_new_revision,
                    sbom_path_in_new_revision,
                )
                success = await interaction.wait_for_task(sbom_task)
                if not success:
                    raise web.FlashError("Internal error: SBOM generation timed out")

            result = await wacp.revision.create_revision_with_quarantine(
                project_key,
                version_key,
                session.uid,
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                description=description,
                modify=modify,
            )

    except Exception as e:
        log.exception("Error generating SBOM:")
        await quart.flash(f"Error generating SBOM: {e!s}", "error")
        return await session.redirect(get.compose.selected, project_key=str(project_key), version_key=str(version_key))

    success = f"SBOM generated for {rel_path.name}"
    if isinstance(result, sql.Quarantined):
        success += ". Archive validation in progress."
    return await session.redirect(
        get.compose.selected,
        success=success,
        project_key=str(project_key),
        version_key=str(version_key),
    )
