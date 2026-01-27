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
from typing import Final

import aiohttp
import asfquart.base as base
import quart

import atr.blueprints.post as post
import atr.db as db
import atr.get as get
import atr.htm as htm
import atr.log as log
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.storage.types as types
import atr.util as util
import atr.web as web

_KEYS_BASE_URL: Final[str] = "https://downloads.apache.org"


@post.committer("/keys/add")
@post.form(shared.keys.AddOpenPGPKeyForm)
async def add(session: web.Committer, add_openpgp_key_form: shared.keys.AddOpenPGPKeyForm) -> web.WerkzeugResponse:
    """Add a new public signing key to the user's account."""
    try:
        key_text = add_openpgp_key_form.public_key
        selected_committee_names = add_openpgp_key_form.selected_committees

        async with storage.write() as write:
            wafc = write.as_foundation_committer()
            ocr: outcome.Outcome[types.Key] = await wafc.keys.ensure_stored_one(key_text)
            key = ocr.result_or_raise()

            for selected_committee_name in selected_committee_names:
                wacp = write.as_committee_participant(selected_committee_name)
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


@post.committer("/keys/details/<fingerprint>")
@post.form(shared.keys.UpdateKeyCommitteesForm)
async def details(
    session: web.Committer, update_form: shared.keys.UpdateKeyCommitteesForm, fingerprint: str
) -> web.WerkzeugResponse:
    """Update committee associations for an OpenPGP key."""
    fingerprint = fingerprint.lower()

    try:
        async with db.session() as data:
            key = await data.public_signing_key(fingerprint=fingerprint, _committees=True).get()
            if not key:
                await quart.flash("OpenPGP key not found", "error")
                return await session.redirect(get.keys.keys)

            if not (key.apache_uid and session.uid and (key.apache_uid.lower() == session.uid.lower())):
                await quart.flash("You are not authorized to modify this key", "error")
                return await session.redirect(get.keys.keys)

            selected_committee_names = update_form.selected_committees
            old_committee_names = {c.name for c in key.committees}

            new_committees = await data.committee(name_in=selected_committee_names).all()
            key.committees = list(new_committees)
            data.add(key)
            await data.commit()

            affected_committee_names = old_committee_names.union(set(selected_committee_names))
            if affected_committee_names:
                async with storage.write() as write:
                    for affected_committee_name in affected_committee_names:
                        wacm = write.as_committee_member_outcome(affected_committee_name).result_or_none()
                        if wacm is None:
                            continue
                        await wacm.keys.autogenerate_keys_file()

            await quart.flash("Key committee associations updated successfully.", "success")
    except Exception as e:
        log.exception("Error updating key committee associations:")
        await quart.flash(f"An unexpected error occurred: {e!s}", "error")

    return await session.redirect(get.keys.details, fingerprint=fingerprint)


@post.committer("/keys/import/<project_name>/<version_name>")
@post.empty()
async def import_selected_revision(
    session: web.Committer, project_name: str, version_name: str
) -> web.WerkzeugResponse:
    async with storage.write() as write:
        wacm = await write.as_project_committee_member(project_name)
        outcomes: outcome.List[types.Key] = await wacm.keys.import_keys_file(project_name, version_name)

    message = f"Uploaded {util.plural(outcomes.result_count, 'key')}"
    if outcomes.error_count > 0:
        message += f", failed to upload {util.plural(outcomes.error_count, 'key')} for {wacm.committee_name}"
    return await session.redirect(
        get.compose.selected,
        success=message,
        project_name=project_name,
        version_name=version_name,
    )


@post.committer("/keys")
@post.form(shared.keys.KeysForm)
async def keys(session: web.Committer, keys_form: shared.keys.KeysForm) -> web.WerkzeugResponse:
    """Handle forms on the keys management page."""
    match keys_form:
        case shared.keys.DeleteOpenPGPKeyForm() as delete_openpgp_form:
            return await _delete_openpgp_key(session, delete_openpgp_form)

        case shared.keys.DeleteSSHKeyForm() as delete_ssh_form:
            return await _delete_ssh_key(session, delete_ssh_form)

        case shared.keys.UpdateCommitteeKeysForm() as update_committee_form:
            return await _update_committee_keys(session, update_committee_form)


@post.committer("/keys/ssh/add")
@post.form(shared.keys.AddSSHKeyForm)
async def ssh_add(session: web.Committer, add_ssh_key_form: shared.keys.AddSSHKeyForm) -> web.WerkzeugResponse:
    """Add a new SSH key to the user's account."""
    try:
        async with storage.write(session) as write:
            wafc = write.as_foundation_committer()
            fingerprint = await wafc.ssh.add_key(add_ssh_key_form.key, session.uid)

        await quart.flash(f"SSH key added successfully: {fingerprint}", "success")
    except util.SshFingerprintError as e:
        await quart.flash(str(e), "error")
    except Exception as e:
        log.exception("Error adding SSH key:")
        await quart.flash(f"An unexpected error occurred: {e!s}", "error")

    return await session.redirect(get.keys.keys)


@post.committer("/keys/upload")
@post.form(shared.keys.UploadKeysForm)
async def upload(session: web.Committer, upload_form: shared.keys.UploadKeysForm) -> str:
    """Upload or fetch a KEYS file containing multiple OpenPGP keys."""
    match upload_form:
        case shared.keys.UploadFileForm() as upload_file_form:
            return await _upload_file_keys(upload_file_form)
        case shared.keys.UploadRemoteForm() as upload_remote_form:
            return await _upload_remote_keys(upload_remote_form)


def _construct_keys_url(committee_name: str, *, is_podling: bool) -> str:
    if is_podling:
        return f"{_KEYS_BASE_URL}/incubator/{committee_name}/KEYS"
    return f"{_KEYS_BASE_URL}/{committee_name}/KEYS"


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
        async with await util.create_secure_session(timeout=timeout) as session:
            async with session.get(keys_url, allow_redirects=True) as response:
                response.raise_for_status()
                return await response.text()
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
    committee_name = update_form.committee_name

    async with storage.write() as write:
        wacm = write.as_committee_member(committee_name)
        match await wacm.keys.autogenerate_keys_file():
            case outcome.Result():
                await quart.flash(
                    f'Successfully regenerated the KEYS file for the "{committee_name}" committee.', "success"
                )
            case outcome.Error():
                await quart.flash(f"Error regenerating the KEYS file for the {committee_name} committee.", "error")

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
            committee = await data.committee(name=selected_committee).get()
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
