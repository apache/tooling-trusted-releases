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
import contextlib
import gc
from typing import Literal

import aiofiles
import aiohttp
import asfquart.base as base
import quart

import atr.blueprints.post as post
import atr.config as config
import atr.db as db
import atr.form as form
import atr.get as get
import atr.htm as htm
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.paths as paths
import atr.sessions as sessions
import atr.shared as shared
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.storage.outcome as outcome
import atr.util as util
import atr.web as web


class PrivateKeyUploadError(Exception):
    pass


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
        key_text = await _add_key_text_resolve(session, add_openpgp_key_form)
        selected_committee_keys = add_openpgp_key_form.selected_committees
        log.keys_submitted("web:keys/add", key_text, committee_keys=selected_committee_keys)

        async with storage.write() as write:
            wafc = write.as_foundation_committer()
            ocr, publications = await wafc.keys.ensure_stored_one(key_text, f"web:{log.get_request_id()}")
            key = ocr.result_or_raise()

            for selected_committee_key in selected_committee_keys:
                wacp = write.as_committee_participant(selected_committee_key)
                oc: outcome.Outcome[datatypes.LinkedCommittee] = await wacp.keys.associate_fingerprint(
                    key.key_model.fingerprint
                )
                linked = oc.result_or_raise()
                publications[linked.name] = linked.publication

            fingerprint_upper = key.key_model.fingerprint.upper()
            if key.status == datatypes.KeyStatus.PARSED:
                details_url = util.as_url(get.keys.details, fingerprint=key.key_model.fingerprint)
                p = htm.p[
                    f"OpenPGP key {fingerprint_upper} was already in the database. ",
                    htm.a(href=details_url)["View key details"],
                    ".",
                ]
                await quart.flash(str(p), "warning")
            elif key.status == datatypes.KeyStatus.REFRESHED:
                await quart.flash(
                    f"OpenPGP key {fingerprint_upper} was updated with the version you uploaded.", "success"
                )
            else:
                await quart.flash(f"OpenPGP key {fingerprint_upper} added successfully.", "success")
            await _flash_openpgp_key_uid_warning(key.key_model, session.asf_uid)
            if notice := shared.keys.publication_added_notice(publications):
                await quart.flash(notice, "info")
            if failure := shared.keys.publication_failed_warning(publications):
                await quart.flash(failure, "error")

    except PrivateKeyUploadError:
        await quart.flash(util.PRIVATE_KEY_UPLOAD_WARNING, "error")
    except datatypes.UnknownApacheUidError as e:
        await quart.flash(str(e), "error")
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
            update = await wafc.keys.update_committee_associations(
                key_fingerprint,
                update_form.selected_committees,
            )

        await quart.flash("Key committee associations updated successfully.", "success")
        added = {c: p for c, p in update.publications.items() if c in update.added}
        removed = {c: p for c, p in update.publications.items() if c in update.removed}
        if notice := shared.keys.publication_added_notice(added):
            await quart.flash(notice, "info")
        if warning := shared.keys.publication_removed_warning(removed):
            await quart.flash(warning, "warning")
        if failure := shared.keys.publication_failed_warning(update.publications):
            await quart.flash(failure, "error")
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
    release = await session.release(project_key, version_key, with_committee=False, with_project=False)
    async with storage.write() as write:
        wacm = await write.as_project_committee_member(project_key)
        async with aiofiles.open(paths.release_directory(release) / "KEYS", "rb") as f:
            keys_content = await f.read(shared.keys.MAX_KEYS_SIZE + 1)
        if len(keys_content) > shared.keys.MAX_KEYS_SIZE:
            await quart.flash(f"KEYS file too large (limit {shared.keys.MAX_KEYS_SIZE} bytes)", "error")
            return await session.redirect(
                get.compose.selected, project_key=str(project_key), version_key=str(version_key)
            )
        keys_text = keys_content.decode("utf-8")
        if util.contains_private_key_text(keys_text):
            del keys_content
            del keys_text
            gc.collect()
            await quart.flash(util.PRIVATE_KEY_UPLOAD_WARNING, "error")
            return await session.redirect(
                get.compose.selected, project_key=str(project_key), version_key=str(version_key)
            )
        log.keys_submitted(
            "web:keys/import",
            keys_text,
            committee_keys=[wacm.committee_key],
            project_key=str(project_key),
            version_key=str(version_key),
        )
        try:
            outcomes, publications = await wacm.keys.import_keys_file(
                project_key,
                version_key,
                f"web:{log.get_request_id()}",
                keys_text,
                release.safe_latest_revision_number,
            )
        except datatypes.RevisionMismatchError:
            await quart.flash("The draft changed during the import, so its KEYS file was left in place", "error")
            return await session.redirect(
                get.compose.selected, project_key=str(project_key), version_key=str(version_key)
            )

    message = f"Uploaded {util.plural(outcomes.result_count, 'key')}"
    if outcomes.error_count > 0:
        message += f", failed to upload {util.plural(outcomes.error_count, 'key')} for {wacm.committee_key}"
    if notice := shared.keys.publication_added_notice(publications):
        await quart.flash(
            f"{notice} Make sure that the published KEYS file contains the imported keys before the release"
            " is announced.",
            "warning",
        )
    if failure := shared.keys.publication_failed_warning(publications):
        await quart.flash(failure, "error")
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

        case shared.keys.SetAutomatedKeysFileForm() as set_automated_form:
            return await _set_automated_keys_file(session, set_automated_form)

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
        key_text = add_ssh_key_form.key
        if util.contains_private_key_text(key_text):
            vars(add_ssh_key_form)["key"] = ""
            session.form_data_discard(["key"])
            del key_text
            gc.collect()
            raise PrivateKeyUploadError
        async with storage.write(session) as write:
            wafc = write.as_foundation_committer()
            fingerprint = await wafc.ssh.add_key(key_text)

        await quart.flash(f"SSH key added successfully: {fingerprint}", "success")
    except PrivateKeyUploadError:
        await quart.flash(util.PRIVATE_KEY_UPLOAD_WARNING, "error")
    except util.SshFingerprintError as e:
        await quart.flash(str(e), "error")
    except Exception as e:
        log.exception("Error adding SSH key:")
        await quart.flash(f"An unexpected error occurred: {e!s}", "error")

    return await session.redirect(get.keys.keys)


@post.typed
async def upload(
    session: web.Committer,
    _keys_upload: Literal["keys/upload"],
    upload_form: shared.keys.UploadKeysForm,
) -> str:
    """
    URL: /keys/upload
    Upload or fetch a KEYS file containing multiple OpenPGP keys.
    """
    match upload_form:
        case shared.keys.UploadFileForm() as upload_file_form:
            return await _upload_file_keys(session, upload_file_form)
        case shared.keys.UploadRemoteForm() as upload_remote_form:
            return await _upload_remote_keys(upload_remote_form)


async def _add_key_text_resolve(session: web.Committer, add_form: shared.keys.AddOpenPGPKeyForm) -> str:
    if (file := add_form.public_key_file) is None:
        key_text = add_form.public_key
        if (len(key_text) > shared.keys.MAX_PUBLIC_KEY_SIZE) or (
            len(key_text.encode()) > shared.keys.MAX_PUBLIC_KEY_SIZE
        ):
            raise web.FlashError(f"Public key too large (limit {shared.keys.MAX_PUBLIC_KEY_SIZE} bytes)")
        if util.contains_private_key_text(key_text):
            vars(add_form)["public_key"] = ""
            session.form_data_discard(["public_key", "public_key_file"])
            del key_text
            gc.collect()
            raise PrivateKeyUploadError
        return key_text
    data = await asyncio.to_thread(file.read)
    if len(data) > shared.keys.MAX_PUBLIC_KEY_SIZE:
        raise web.FlashError(f"Uploaded key file too large (limit {shared.keys.MAX_PUBLIC_KEY_SIZE} bytes)")
    try:
        key_text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise web.FlashError(f"Uploaded key file is not valid UTF-8: {e}")
    if util.contains_private_key_text(key_text):
        vars(add_form)["public_key"] = ""
        vars(add_form)["public_key_file"] = None
        session.form_data_discard(["public_key", "public_key_file"])
        with contextlib.suppress(Exception):
            await asyncio.to_thread(file.close)
        del data
        del file
        del key_text
        gc.collect()
        raise PrivateKeyUploadError
    return key_text


async def _delete_openpgp_key(
    session: web.Committer, delete_form: shared.keys.DeleteOpenPGPKeyForm
) -> web.WerkzeugResponse:
    """Delete an OpenPGP key from the user's account."""
    fingerprint = delete_form.fingerprint

    async with storage.write() as write:
        wafc = write.as_foundation_committer()
        oc: outcome.Outcome[datatypes.KeyDeletion] = await wafc.keys.delete_key(
            fingerprint, f"web:{log.get_request_id()}"
        )

    match oc:
        case outcome.Result(deletion):
            if warning := shared.keys.publication_removed_warning(deletion.publications):
                await quart.flash(warning, "warning")
            if failure := shared.keys.publication_failed_warning(deletion.publications):
                await quart.flash(failure, "error")
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

    # audit_guidance revoking an SSH key terminates all of this user's OAuth sessions, in case the key was uploaded
    # by an attacker-controlled session
    await sessions.terminate_current_users_sessions(session.uid)
    return await session.redirect(get.keys.keys)


async def _fetch_keys_from_url(keys_url: str) -> str:
    """Fetch KEYS file from ASF downloads."""
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with util.create_secure_session(timeout=timeout) as session:
            # audit_guidance known issue: redirect without domain validation; will change when key import is refactored
            async with session.get(keys_url, allow_redirects=True) as response:
                response.raise_for_status()
                content_length = response.content_length
                if (content_length is not None) and (content_length > shared.keys.MAX_KEYS_SIZE):
                    raise base.ASFQuartException(
                        f"KEYS file too large ({content_length} bytes, limit {shared.keys.MAX_KEYS_SIZE})",
                        errorcode=502,
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > shared.keys.MAX_KEYS_SIZE:
                        raise base.ASFQuartException(
                            f"KEYS file too large (limit {shared.keys.MAX_KEYS_SIZE} bytes)",
                            errorcode=502,
                        )
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8")
    except aiohttp.ClientResponseError as e:
        raise base.ASFQuartException(f"Unable to fetch keys from remote server: {e.status} {e.message}", errorcode=502)
    except aiohttp.ClientError as e:
        raise base.ASFQuartException(f"Network error while fetching keys: {e}", errorcode=503)


async def _flash_openpgp_key_uid_warning(key_model: sql.SigningCertificate, current_asf_uid: str) -> None:
    warning = _openpgp_key_uid_warning(key_model, current_asf_uid)
    if warning is not None:
        await quart.flash(str(warning), "warning")


def _openpgp_key_uid_warning(key_model: sql.SigningCertificate, current_asf_uid: str) -> htm.Element | None:
    fingerprint_upper = key_model.fingerprint.upper()
    if key_model.apache_uid is None:
        return None
    if key_model.apache_uid.lower() != current_asf_uid.lower():
        details_url = util.as_url(get.keys.details, fingerprint=key_model.fingerprint)
        return htm.p[
            f"OpenPGP key {fingerprint_upper} was uploaded and associated, but it appears to belong to ASF UID "
            f"{key_model.apache_uid}, not {current_asf_uid}. ",
            htm.a(href=details_url)["Review key details"],
            ".",
        ]

    return None


async def _process_keys(keys_text: str, selected_committee: str) -> str:
    """Process keys text and associate with committee."""
    if util.contains_private_key_text(keys_text):
        del keys_text
        gc.collect()
        await quart.flash(util.PRIVATE_KEY_UPLOAD_WARNING, "error")
        return await shared.keys.render_upload_page(error=True)

    async with storage.write() as write:
        wacp = write.as_committee_participant(selected_committee)
        outcomes, publications = await wacp.keys.ensure_associated(keys_text, f"web:{log.get_request_id()}")

    success_count = outcomes.result_count
    error_count = outcomes.error_count
    total_count = success_count + error_count

    message = f"Processed {util.plural(total_count, 'key')}: {success_count} successful"
    if error_count > 0:
        message += f", {error_count} failed"

    await quart.flash(message, "success" if (success_count > 0) else "error")
    if notice := shared.keys.publication_added_notice(publications):
        await quart.flash(notice, "info")
    if failure := shared.keys.publication_failed_warning(publications):
        await quart.flash(failure, "error")

    return await shared.keys.render_upload_page(results=outcomes, submitted_committees=[selected_committee])


async def _set_automated_keys_file(
    session: web.Committer, set_form: shared.keys.SetAutomatedKeysFileForm
) -> web.WerkzeugResponse:
    committee_key = set_form.committee_key
    enabled = set_form.enabled == "true"

    async with storage.write() as write:
        wacm = write.as_committee_member(committee_key)
        changed = await wacm.keys.set_automated_keys_file(enabled)

    state = "enabled" if enabled else "disabled"
    already = "now" if changed else "already"
    message = f'Automated KEYS publication is {already} {state} for the "{committee_key}" committee.'
    if enabled and (not config.get().SVN_PUBLISH_URL):
        message += " Note that SVN publication is not configured on this server."
    return await session.redirect(get.committees.view, success=message, name=committee_key)


async def _update_committee_keys(
    session: web.Committer, update_form: shared.keys.UpdateCommitteeKeysForm
) -> web.WerkzeugResponse:
    """Regenerate the KEYS file for a committee."""
    committee_key = update_form.committee_key

    async with storage.write() as write:
        wacm = write.as_committee_member(committee_key)
        keys_outcome, publication = await wacm.keys.autogenerate_keys_file()
        match keys_outcome:
            case outcome.Result():
                base = f'Regenerated the KEYS file for the "{committee_key}" committee'
                match publication:
                    case outcome.Result(datatypes.KeysPublish.PUBLISHED):
                        await quart.flash(f"{base} and published it to SVN.", "success")
                    case outcome.Result(datatypes.KeysPublish.SVN_NOT_CONFIGURED):
                        await quart.flash(f"{base}.", "success")
                    case outcome.Result(datatypes.KeysPublish.AUTOMATION_DISABLED):
                        await quart.flash(
                            f"{base}, but it was not published to SVN"
                            " because publication is disabled for this committee.",
                            "warning",
                        )
                    case outcome.Error(error):
                        await quart.flash(f"{base}, but publishing to SVN failed: {error}", "warning")
            case outcome.Error():
                await quart.flash(f"Error regenerating the KEYS file for the {committee_key} committee.", "error")

    return await session.redirect(get.keys.keys)


async def _upload_file_keys(session: web.Committer, upload_file_form: shared.keys.UploadFileForm) -> str:
    """Handle file upload."""
    try:
        uploaded_file = upload_file_form.key
        if uploaded_file is None:
            await quart.flash("No KEYS file uploaded", "error")
            return await shared.keys.render_upload_page(error=True)

        keys_content = await asyncio.to_thread(uploaded_file.read)
        if len(keys_content) > shared.keys.MAX_KEYS_SIZE:
            await quart.flash(f"KEYS file too large (limit {shared.keys.MAX_KEYS_SIZE} bytes)", "error")
            return await shared.keys.render_upload_page(error=True)
        keys_text = keys_content.decode("utf-8", errors="replace")
        if util.contains_private_key_text(keys_text):
            vars(upload_file_form)["key"] = None
            session.form_data_discard(["key"])
            with contextlib.suppress(Exception):
                await asyncio.to_thread(uploaded_file.close)
            del keys_content
            del keys_text
            del uploaded_file
            gc.collect()
            await quart.flash(util.PRIVATE_KEY_UPLOAD_WARNING, "error")
            return await shared.keys.render_upload_page(error=True)

        if not keys_text:
            await quart.flash("No KEYS data found", "error")
            return await shared.keys.render_upload_page(error=True)

        selected_committee = upload_file_form.selected_committee
        log.keys_submitted("web:keys/upload", keys_text, committee_keys=[selected_committee])
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
        keys_url = paths.committee_keys_url(committee)
        keys_text = await _fetch_keys_from_url(keys_url)

        if util.contains_private_key_text(keys_text):
            del keys_text
            gc.collect()
            await quart.flash(util.PRIVATE_KEY_UPLOAD_WARNING, "error")
            return await shared.keys.render_upload_page(error=True)
        if not keys_text:
            await quart.flash("No KEYS data found at ASF downloads", "error")
            return await shared.keys.render_upload_page(error=True)

        log.keys_submitted("web:keys/upload:remote", keys_text, committee_keys=[selected_committee], url=keys_url)
        return await _process_keys(keys_text, selected_committee)
    except Exception as e:
        log.exception("Error fetching KEYS file from ASF:")
        await quart.flash(f"Error fetching KEYS file: {e!s}", "error")
        return await shared.keys.render_upload_page(error=True)
