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

from typing import TYPE_CHECKING, Literal

import aiofiles.os
import asfquart.base as base
import quart

import atr.blueprints.post as post
import atr.db.interaction as interaction
import atr.form as form
import atr.get as get
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web

if TYPE_CHECKING:
    import pathlib


@post.typed
async def cache_reset(
    session: web.Committer,
    _draft_reset: Literal["draft/reset"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
    _form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /draft/reset/<project_name>/<version_name>
    Start a new draft revision and switch this release to global caching.
    """
    if not session.is_admin:
        raise base.ASFQuartException("Admin access required", errorcode=403)

    description = "Empty revision to restart all checks without cache for the whole release candidate draft"
    async with storage.write(session) as write:
        wacp = await write.as_project_committee_participant(project_name)
        result = await wacp.revision.create_revision_with_quarantine(
            project_name,
            version_name,
            session.uid,
            description=description,
            reset_to_global_cache=True,
        )

    success = "Release set back to global caching"
    if isinstance(result, sql.Quarantined):
        success += ". Archive validation in progress."
    return await session.redirect(
        get.compose.selected,
        project_name=str(project_name),
        version_name=str(version_name),
        success=success,
    )


@post.typed
async def delete(
    session: web.Committer,
    _compose: Literal["compose"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
    _form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /compose/<project_name>/<version_name>
    Delete a candidate draft and all its associated files.
    """
    # Delete the metadata from the database
    async with storage.write(session) as write:
        wacp = await write.as_project_committee_participant(project_name)
        error = await wacp.release.delete(
            project_name,
            version_name,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            include_downloads=False,
        )
        # Ensure that deletion errors are reported to the user
        if error is not None:
            await quart.flash(f"Error deleting candidate draft: {error}", "error")
            return await session.redirect(
                get.compose.selected, project_name=str(project_name), version_name=str(version_name)
            )

    return await session.redirect(get.root.index, success="Candidate draft deleted successfully")


@post.typed
async def delete_file(
    session: web.Committer,
    _draft_delete_file: Literal["draft/delete-file"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
    delete_file_form: shared.draft.DeleteFileForm,
) -> web.WerkzeugResponse:
    """
    URL: /draft/delete-file/<project_name>/<version_name>
    Delete a specific file from the release candidate, creating a new revision.
    """
    rel_path_to_delete = delete_file_form.file_path
    if rel_path_to_delete is None:
        await quart.flash("No file path specified", "error")
        return await session.redirect(
            get.compose.selected, project_name=str(project_name), version_name=str(version_name)
        )

    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_name)
            metadata_files_deleted = await wacp.release.delete_file(project_name, version_name, rel_path_to_delete)
    except Exception as e:
        log.exception("Error deleting file:")
        await quart.flash(f"Error deleting file: {e!s}", "error")
        return await session.redirect(
            get.compose.selected, project_name=str(project_name), version_name=str(version_name)
        )

    success_message = f"File '{rel_path_to_delete.name}' deleted successfully"
    if metadata_files_deleted:
        success_message += f", and {util.plural(metadata_files_deleted, 'associated metadata file')} deleted"
    return await session.redirect(
        get.compose.selected, success=success_message, project_name=str(project_name), version_name=str(version_name)
    )


@post.typed
async def hashgen(
    session: web.Committer,
    _draft_hashgen: Literal["draft/hashgen"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
    file_path: unsafe.Path,
    empty_form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /draft/hashgen/<project_name>/<version_name>/<file_path>
    Generate an sha512 hash file for a candidate draft file, creating a new revision.
    """
    rel_path = form.to_relpath(file_path)
    if rel_path is None:
        await quart.flash("Invalid file path", "error")
        return await session.redirect(
            get.compose.selected, project_name=str(project_name), version_name=str(version_name)
        )

    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_name)
            await wacp.release.generate_hash_file(project_name, version_name, rel_path)

    except Exception as e:
        log.exception("Error generating hash file:")
        await quart.flash(f"Error generating hash file: {e!s}", "error")
        return await session.redirect(
            get.compose.selected, project_name=str(project_name), version_name=str(version_name)
        )

    return await session.redirect(
        get.compose.selected,
        success="SHA512 file generated successfully",
        project_name=str(project_name),
        version_name=str(version_name),
    )


@post.typed
async def quarantine_clear(
    session: web.Committer,
    _draft_quarantine_clear: Literal["draft/quarantine/clear"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
    clear_form: shared.draft.ClearQuarantineForm,
) -> web.WerkzeugResponse:
    """URL: /draft/quarantine/clear/<project_name>/<version_name>"""
    async with storage.write(session) as write:
        wacp = await write.as_project_committee_participant(project_name)
        await wacp.revision.clear_quarantine(project_name, version_name, clear_form.quarantined_id)

    return await session.redirect(
        get.compose.selected,
        project_name=str(project_name),
        version_name=str(version_name),
        success="Quarantine failure dismissed",
    )


@post.typed
async def recheck(
    session: web.Committer,
    _draft_recheck: Literal["draft/recheck"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
    empty_form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /draft/recheck/<project_name>/<version_name>
    Start a new draft revision and switch this release to release-local caching.
    """
    if not session.is_admin:
        raise base.ASFQuartException("Admin access required", errorcode=403)

    description = "Empty revision to restart all checks without cache for the whole release candidate draft"
    async with storage.write(session) as write:
        wacp = await write.as_project_committee_participant(project_name)
        result = await wacp.revision.create_revision_with_quarantine(
            project_name,
            version_name,
            session.uid,
            description=description,
            set_local_cache=True,
        )

    success = "All checks restarted with release-local cache"
    if isinstance(result, sql.Quarantined):
        success += ". Archive validation in progress."
    return await session.redirect(
        get.compose.selected,
        project_name=str(project_name),
        version_name=str(version_name),
        success=success,
    )


@post.typed
async def sbomgen(
    session: web.Committer,
    _draft_sbomgen: Literal["draft/sbomgen"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
    file_path: unsafe.Path,
    empty_form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /draft/sbomgen/<project_name>/<version_name>/<file_path>
    Generate a CycloneDX SBOM file for a candidate draft file, creating a new revision.
    """
    path = str(file_path)
    rel_path = form.to_relpath(path)
    if rel_path is None:
        await quart.flash("Invalid file path", "error")
        return await session.redirect(
            get.compose.selected, project_name=str(project_name), version_name=str(version_name)
        )

    # Check that the file is a .tar.gz archive before creating a revision
    if not (path.endswith(".tar.gz") or path.endswith(".tgz") or path.endswith(".zip") or path.endswith(".jar")):
        raise base.ASFQuartException(
            f"SBOM generation requires .tar.gz, .tgz, .zip or .jar files. Received: {path}", errorcode=400
        )

    try:
        description = "SBOM generation through web interface"
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_name)

            async def modify(path: pathlib.Path, old_rev: sql.Revision | None) -> None:
                path_in_new_revision = path / rel_path
                sbom_path_rel = rel_path.with_suffix(rel_path.suffix + ".cdx.json").name
                sbom_path_in_new_revision = path / rel_path.parent / sbom_path_rel

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
                    project_name,
                    version_name,
                    old_rev.number,
                    path_in_new_revision,
                    sbom_path_in_new_revision,
                )
                success = await interaction.wait_for_task(sbom_task)
                if not success:
                    raise web.FlashError("Internal error: SBOM generation timed out")

            result = await wacp.revision.create_revision_with_quarantine(
                project_name, version_name, session.uid, description=description, modify=modify
            )

    except Exception as e:
        log.exception("Error generating SBOM:")
        await quart.flash(f"Error generating SBOM: {e!s}", "error")
        return await session.redirect(
            get.compose.selected, project_name=str(project_name), version_name=str(version_name)
        )

    success = f"SBOM generated for {rel_path.name}"
    if isinstance(result, sql.Quarantined):
        success += ". Archive validation in progress."
    return await session.redirect(
        get.compose.selected,
        success=success,
        project_name=str(project_name),
        version_name=str(version_name),
    )
