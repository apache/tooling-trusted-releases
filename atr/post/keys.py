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


import asyncio
from typing import Final, Literal

import aiohttp
import asfquart.base as base
import quart

import atr.blueprints.post as post
import atr.db as db
import atr.form as form
import atr.get as get
import atr.htm as htm
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.shared as shared
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.storage.types as types
import atr.util as util
import atr.web as web

_KEYS_BASE_URL: Final[str] = "https://downloads.apache.org"

# The Apache Subversion KEYS file is largest at 3732091 bytes
_MAX_KEYS_SIZE: Final[int] = 10 * 1024 * 1024


@post.typed
async def add(
    session: web.Committer,
    _keys_add: Literal["keys/add"],
    add_openpgp_key_form: shared.keys.AddOpenPGPKeyForm,
) -> web.WerkzeugResponse:
    """
    URL: /keys/add
    Add a new public signing key to the user's account.
    """
    try:
        key_text = add_openpgp_key_form.public_key
        selected_committee_keys = add_openpgp_key_form.selected_committees

        async with storage.write() as write:
            wafc = write.as_foundation_committer()
            ocr: outcome.Outcome[types.Key] = await wafc.keys.ensure_stored_one(key_text)
            key = ocr.result_or_raise()

            for selected_committee_key in selected_committee_keys:
                wacp = write.as_committee_participant(selected_committee_key)
                oc: outcome.Outcome[types.LinkedCommittee] = await wacp.keys.associate_fingerprint(
                    key.key_model.fingerprint
                )
                oc.result_or_raise()

            fingerprint_upper = key.key_model.fingerprint.upper()
            if key.status == types.KeyStatus.PARSED:
                details_url = util.as_url(get.keys.details, fingerprint=key.key_model.fingerprint)
                p = htm.p[
                    f"OpenPGP key {fingerprint_upper} was already in the database. ",
                    htm.a(href=details_url)["View key details"],
                    ".",
                ]
                await quart.flash(str(p), "warning")
            else:
                await quart.flash(f"OpenPGP key {fingerprint_upper} added successfully.", "success")

    except web.FlashError as e:
        log.warning(f"FlashError adding OpenPGP key: {e}")
        await quart.flash(str(e), "error")
    except Exception as e:
        log.exception("Error adding OpenPGP key:")
        await quart.flash(f"An unexpected error occurred: {e!s}", "error")

    return await session.redirect(get.keys.keys)


@post.typed
async def details(
    session: web.Committer,
    _keys_details: Literal["keys/details"],
    fingerprint: unsafe.UnsafeStr,
    update_form: shared.keys.UpdateKeyCommitteesForm,
) -> web.WerkzeugResponse:
    """
    URL: /keys/details/<fingerprint>
    Update committee associations for an OpenPGP key.
    """
    key_fingerprint = str(fingerprint).lower()

    try:
        async with storage.write() as write:
            wafc = write.as_foundation_committer()
            await wafc.keys.update_committee_associations(
                key_fingerprint,
                update_form.selected_committees,
            )

        await quart.flash("Key committee associations updated successfully.", "success")
    except storage.AccessError as e:
        await quart.flash(str(e), "error")
    except Exception as e:
        log.exception("Error updating key committee associations:")
        await quart.flash(f"An unexpected error occurred: {e!s}", "error")

    return await session.redirect(get.keys.details, fingerprint=key_fingerprint)


@post.typed
async def import_selected_revision(
    session: web.Committer,
    _keys_import: Literal["keys/import"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    _form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /keys/import/<project_key>/<version_key>
    """
    await session.check_access(project_key)
    await session.release(project_key, version_key, with_committee=False, with_project=False)
    async with storage.write() as write:
        wacm = await write.as_project_committee_member(project_key)
        outcomes: outcome.List[types.Key] = await wacm.keys.import_keys_file(project_key, version_key)

    message = f"Uploaded {util.plural(outcomes.result_count, 'key')}"
    if outcomes.error_count > 0:
        message += f", failed to upload {util.plural(outcomes.error_count, 'key')} for {wacm.committee_key}"
    return await session.redirect(
        get.compose.selected,
        success=message,
        project_key=str(project_key),
        version_key=str(version_key),
    )


@post.typed
async def keys(
    session: web.Committer,
    _keys: Literal["keys"],
    keys_form: shared.keys.KeysForm,
) -> web.WerkzeugResponse:
    """
    URL: /keys
    Handle forms on the keys management page.
    """
    match keys_form:
        case shared.keys.DeleteOpenPGPKeyForm() as delete_openpgp_form:
            return await _delete_openpgp_key(session, delete_openpgp_form)

        case shared.keys.DeleteSSHKeyForm() as delete_ssh_form:
            return await _delete_ssh_key(session, delete_ssh_form)

        case shared.keys.UpdateCommitteeKeysForm() as update_committee_form:
            return await _update_committee_keys(session, update_committee_form)


@post.typed
async def ssh_add(
    session: web.Committer,
    _keys_ssh_add: Literal["keys/ssh/add"],
    add_ssh_key_form: shared.keys.AddSSHKeyForm,
) -> web.WerkzeugResponse:
    """
    URL: /keys/ssh/add
    Add a new SSH key to the user's account.
    """
    try:
        async with storage.write(session) as write:
            wafc = write.as_foundation_committer()
            fingerprint = await wafc.ssh.add_key(add_ssh_key_form.key)

        await quart.flash(f"SSH key added successfully: {fingerprint}", "success")
    except util.SshFingerprintError as e:
        await quart.flash(str(e), "error")
    except Exception as e:
        log.exception("Error adding SSH key:")
        await quart.flash(f"An unexpected error occurred: {e!s}", "error")

    return await session.redirect(get.keys.keys)


@post.typed
async def upload(
    _session: web.Committer,
    _keys_upload: Literal["keys/upload"],
    upload_form: shared.keys.UploadKeysForm,
) -> str:
    """
    URL: /keys/upload
    Upload or fetch a KEYS file containing multiple OpenPGP keys.
    """
    match upload_form:
        case shared.keys.UploadFileForm() as upload_file_form:
            return await _upload_file_keys(upload_file_form)
        case shared.keys.UploadRemoteForm() as upload_remote_form:
            return await _upload_remote_keys(upload_remote_form)


def _construct_keys_url(committee_key: str, *, is_podling: bool) -> str:
    if is_podling:
        return f"{_KEYS_BASE_URL}/incubator/{committee_key}/KEYS"
    return f"{_KEYS_BASE_URL}/{committee_key}/KEYS"


async def _delete_openpgp_key(
    session: web.Committer, delete_form: shared.keys.DeleteOpenPGPKeyForm
) -> web.WerkzeugResponse:
    """Delete an OpenPGP key from the user's account."""
    fingerprint = delete_form.fingerprint

    async with storage.write() as write:
        wafc = write.as_foundation_committer()
        oc: outcome.Outcome[sql.PublicSigningKey] = await wafc.keys.delete_key(fingerprint)

    match oc:
        case outcome.Result():
            return await session.redirect(get.keys.keys, success="OpenPGP key deleted successfully")
        case outcome.Error(error):
            return await session.redirect(get.keys.keys, error=f"Error deleting OpenPGP key: {error}")


async def _delete_ssh_key(session: web.Committer, delete_form: shared.keys.DeleteSSHKeyForm) -> web.WerkzeugResponse:
    """Delete an SSH key from the user's account."""
    fingerprint = delete_form.fingerprint

    async with storage.write() as write:
        wafc = write.as_foundation_committer()
        try:
            await wafc.ssh.delete_key(fingerprint)
        except storage.AccessError as e:
            return await session.redirect(get.keys.keys, error=f"Error deleting SSH key: {e}")

    return await session.redirect(get.keys.keys, success="SSH key deleted successfully")


async def _fetch_keys_from_url(keys_url: str) -> str:
    """Fetch KEYS file from ASF downloads."""
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with util.create_secure_session(timeout=timeout) as session:
            # audit_guidance known issue: redirect without domain validation; will change when key import is refactored
            async with session.get(keys_url, allow_redirects=True) as response:
                response.raise_for_status()
                content_length = response.content_length
                if (content_length is not None) and (content_length > _MAX_KEYS_SIZE):
                    raise base.ASFQuartException(
                        f"KEYS file too large ({content_length} bytes, limit {_MAX_KEYS_SIZE})",
                        errorcode=502,
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > _MAX_KEYS_SIZE:
                        raise base.ASFQuartException(
                            f"KEYS file too large (limit {_MAX_KEYS_SIZE} bytes)",
                            errorcode=502,
                        )
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8")
    except aiohttp.ClientResponseError as e:
        raise base.ASFQuartException(f"Unable to fetch keys from remote server: {e.status} {e.message}", errorcode=502)
    except aiohttp.ClientError as e:
        raise base.ASFQuartException(f"Network error while fetching keys: {e}", errorcode=503)


async def _process_keys(keys_text: str, selected_committee: str) -> str:
    """Process keys text and associate with committee."""
    async with storage.write() as write:
        wacp = write.as_committee_participant(selected_committee)
        outcomes = await wacp.keys.ensure_associated(keys_text)

    success_count = outcomes.result_count
    error_count = outcomes.error_count
    total_count = success_count + error_count

    message = f"Processed {util.plural(total_count, 'key')}: {success_count} successful"
    if error_count > 0:
        message += f", {error_count} failed"

    await quart.flash(message, "success" if (success_count > 0) else "error")

    return await shared.keys.render_upload_page(results=outcomes, submitted_committees=[selected_committee])


async def _update_committee_keys(
    session: web.Committer, update_form: shared.keys.UpdateCommitteeKeysForm
) -> web.WerkzeugResponse:
    """Regenerate the KEYS file for a committee."""
    committee_key = update_form.committee_key

    async with storage.write() as write:
        wacm = write.as_committee_member(committee_key)
        match await wacm.keys.autogenerate_keys_file():
            case outcome.Result():
                await quart.flash(
                    f'Successfully regenerated the KEYS file for the "{committee_key}" committee.', "success"
                )
            case outcome.Error():
                await quart.flash(f"Error regenerating the KEYS file for the {committee_key} committee.", "error")

    return await session.redirect(get.keys.keys)


async def _upload_file_keys(upload_file_form: shared.keys.UploadFileForm) -> str:
    """Handle file upload."""
    try:
        if upload_file_form.key is None:
            await quart.flash("No KEYS file uploaded", "error")
            return await shared.keys.render_upload_page(error=True)

        keys_content = await asyncio.to_thread(upload_file_form.key.read)
        keys_text = keys_content.decode("utf-8", errors="replace")

        if not keys_text:
            await quart.flash("No KEYS data found", "error")
            return await shared.keys.render_upload_page(error=True)

        selected_committee = upload_file_form.selected_committee
        return await _process_keys(keys_text, selected_committee)
    except Exception as e:
        log.exception("Error uploading KEYS file:")
        await quart.flash(f"Error processing KEYS file: {e!s}", "error")
        return await shared.keys.render_upload_page(error=True)


async def _upload_remote_keys(upload_remote_form: shared.keys.UploadRemoteForm) -> str:
    """Fetch KEYS file from ASF downloads."""
    try:
        selected_committee = upload_remote_form.committee
        async with db.session() as data:
            committee = await data.committee(key=selected_committee).get()
            if not committee:
                await quart.flash(f"Committee '{selected_committee}' not found", "error")
                return await shared.keys.render_upload_page(error=True)
            is_podling = committee.is_podling

        keys_url = _construct_keys_url(selected_committee, is_podling=is_podling)
        keys_text = await _fetch_keys_from_url(keys_url)

        if not keys_text:
            await quart.flash("No KEYS data found at ASF downloads", "error")
            return await shared.keys.render_upload_page(error=True)

        return await _process_keys(keys_text, selected_committee)
    except Exception as e:
        log.exception("Error fetching KEYS file from ASF:")
        await quart.flash(f"Error fetching KEYS file: {e!s}", "error")
        return await shared.keys.render_upload_page(error=True)
