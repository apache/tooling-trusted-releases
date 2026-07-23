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

# TODO: Always raise and catch AccessError

# Removing this will cause circular imports
from __future__ import annotations

import asyncio
import datetime
import pathlib
import shutil
import tempfile
import textwrap
from typing import Final, NoReturn

import aiofiles
import aiofiles.os
import openpgp
import sqlalchemy.dialects.sqlite as sqlite
import sqlmodel

import atr.cache as cache
import atr.config as config
import atr.db as db
import atr.log as log
import atr.models.basic as basic
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.pgp as pgp
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.storage.outcome as outcome
import atr.svn as svn
import atr.tasks as tasks
import atr.user as user
import atr.util as util

_ALGORITHM_IDS: Final[dict[str, int]] = {
    "rsa": 1,
    "rsa-encrypt": 2,
    "rsa-sign": 3,
    "elgamal-encrypt": 16,
    "dsa": 17,
    "ecdh": 18,
    "ecdsa": 19,
    "elgamal": 20,
    "diffie-hellman": 21,
    "eddsa-legacy": 22,
    "x25519": 25,
    "x448": 26,
    "ed25519": 27,
    "ed448": 28,
}
_ALGORITHM_NAMES: Final[dict[int, str]] = {
    1: "RSA",
    2: "RSA",
    3: "RSA",
    16: "Elgamal",
    17: "DSA",
    18: "ECDH",
    19: "ECDSA",
    20: "Elgamal",
    21: "Diffie-Hellman",
    22: "EdDSA",
    25: "X25519",
    26: "X448",
    27: "Ed25519",
    28: "Ed448",
}
_APPROVED_KEY_ALGORITHMS: Final = frozenset({1, 3, 19, 22, 27})
_EC_MINIMUM_BITS: Final = 255
_RSA_ALGORITHMS: Final = frozenset({1, 3})
_RSA_MINIMUM_BITS: Final = 4096


def _algorithm_id(name: str) -> int:
    if algorithm_id := _ALGORITHM_IDS.get(name):
        return algorithm_id
    raise ValueError(f"Unsupported OpenPGP key algorithm: {name}")


def _algorithm_name(algorithm: int) -> str:
    return _ALGORITHM_NAMES.get(algorithm, str(algorithm))


def _key_length(key: openpgp.PublicKey) -> int:
    bits = pgp.public_params_bits(key.public_params)
    if bits is None:
        raise ValueError(f"Key size is not available for algorithm {key.public_key_algorithm}")
    return bits


def _block_downgrade_reason(stored_block: str, incoming_block: str) -> str | None:
    # Self-signatures are append-only, so a re-upload may add declarations but must not roll the
    # effective state back: no revocation dropped, no regression to an older self-signature. A minimised
    # re-export keeps both and passes. A block we can't parse can't be judged, so allow it through
    try:
        stored_key, _ = openpgp.PublicKey.from_armor(stored_block)
        incoming_key, _ = openpgp.PublicKey.from_armor(incoming_block)
    except Exception:
        return None
    dropped = pgp.revocations_dropped(stored_key, incoming_key)
    if dropped:
        return f"drops the revocation on {', '.join(sorted(dropped))}"
    stored_latest = pgp.latest_self_signature_created_at(stored_key)
    incoming_latest = pgp.latest_self_signature_created_at(incoming_key)
    if (stored_latest is not None) and ((incoming_latest is None) or (incoming_latest < stored_latest)):
        return "predates the latest stored self-signature"
    return None


def _signing_key_rows(certificate_fingerprint: str, block: str | bytes) -> list[dict] | None:
    """The SigningKey rows a certificate's block describes, or None if the block is for another key."""
    if isinstance(block, bytes):
        block = block.decode("utf-8", errors="replace")
    key, _ = openpgp.PublicKey.from_armor(block)
    if key.fingerprint.lower() != certificate_fingerprint.lower():
        return None
    return [
        {
            "fingerprint": facts.fingerprint,
            "certificate_fingerprint": certificate_fingerprint.lower(),
            "is_primary": facts.is_primary,
            "key_id": facts.key_id,
            "algorithm": _algorithm_id(facts.algorithm),
            "length": facts.length_bits or 0,
            "created": facts.created,
            "expires": facts.expires,
            "revoked": facts.revoked,
            "can_sign": facts.can_sign,
        }
        for facts in pgp.signing_key_facts(key)
    ]


async def _sync_signing_keys(data: db.Session, certificates: list[sql.SigningCertificate]) -> None:
    """Bring each certificate's SigningKey rows into line with the block it holds"""
    rows = []
    for certificate in certificates:
        try:
            derived = _signing_key_rows(certificate.fingerprint, certificate.ascii_armored_key)
        except Exception as e:
            log.warning(f"Could not derive signing keys for {certificate.fingerprint}: {e}")
            continue
        if derived is None:
            # The block belongs to another key, so anything derived from it would describe that one
            log.warning(f"Stored block for {certificate.fingerprint} holds another key, skipping")
            continue
        rows.extend(derived)
    if not rows:
        return
    statement = sqlite.insert(sql.SigningKey)
    # A refreshed block can move expiry, revoke a subkey, or add one, so every column is rewritten.
    # Subkeys which have disappeared keep their row, since an artifact may still be attributed to one
    await data.execute(
        statement.on_conflict_do_update(
            index_elements=["fingerprint"],
            set_={
                "certificate_fingerprint": statement.excluded.certificate_fingerprint,
                "is_primary": statement.excluded.is_primary,
                "key_id": statement.excluded.key_id,
                "algorithm": statement.excluded.algorithm,
                "length": statement.excluded.length,
                "created": statement.excluded.created,
                "expires": statement.excluded.expires,
                "revoked": statement.excluded.revoked,
                "can_sign": statement.excluded.can_sign,
            },
        ),
        rows,
    )


class GeneralPublic:
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsGeneralPublic,
        data: db.Session,
    ):
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        self.__asf_uid = write.authorisation.asf_uid


class FoundationCommitter(GeneralPublic):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationCommitter, data: db.Session):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid

        # Specific to this module
        self.__key_block_models_cache = {}

    async def delete_key(self, fingerprint: str) -> outcome.Outcome[datatypes.KeyDeletion]:
        try:
            via = sql.validate_instrumented_attribute
            key = await self.__data.signing_certificate(
                fingerprint=fingerprint,
                apache_uid=self.__asf_uid,
                _committees=True,
            ).demand(storage.AccessError(f"Key not found: {fingerprint}", status=404))
            affected_committee_keys = {committee.key for committee in key.committees}
            update_result = await self.__data.execute(
                sqlmodel.update(sql.SigningCertificate)
                .where(
                    via(sql.SigningCertificate.fingerprint) == key.fingerprint,
                    via(sql.SigningCertificate.apache_uid) == self.__asf_uid,
                    via(sql.SigningCertificate.deleted).is_(None),
                )
                .values(deleted=datetime.datetime.now(datetime.UTC))
            )
            if getattr(update_result, "rowcount", 0) != 1:
                raise storage.AccessError(f"Key not found: {fingerprint}", status=404)
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                action="key_delete",
                asf_uid=self.__asf_uid,
                fingerprint=key.fingerprint,
                committee_keys=[k for k in sorted(affected_committee_keys)],
            )
            publications: dict[str, outcome.Outcome[datatypes.KeysPublish]] = {}
            for committee_key in sorted(affected_committee_keys):
                _, publication = await self._sync_committee_keys_file(committee_key)
                publications[committee_key] = publication
            await self._recheck_committee_drafts(*affected_committee_keys)
            return outcome.Result(datatypes.KeyDeletion(key=key, publications=publications))
        except Exception as e:
            return outcome.Error(e)

    async def ensure_stored_one(
        self, key_file_text: str
    ) -> tuple[outcome.Outcome[datatypes.Key], dict[str, outcome.Outcome[datatypes.KeysPublish]]]:
        return await self.__ensure_one(key_file_text, associate=False)

    def public_key_model(
        self,
        key: openpgp.PublicKey,
        ldap_data: cache.EmailUidLookup,
        original_key_block: str | None = None,
    ) -> sql.SigningCertificate:
        uids = list(key.user_ids)
        asf_uid = self.__uids_asf_uid(uids, ldap_data)
        if not uids:
            raise ValueError("No UIDs found in key")

        # Use the original key block if available
        ascii_armored = original_key_block if original_key_block else key.to_armored()

        return sql.SigningCertificate(
            fingerprint=key.fingerprint.lower(),
            latest_self_signature=pgp.latest_self_signature_created_at(key),
            primary_declared_uid=uids[0],
            secondary_declared_uids=uids[1:],
            apache_uid=asf_uid,
            ascii_armored_key=ascii_armored,
        )

    async def keys_file_text(self, committee_key: str) -> str:
        committee = await self.__data.committee(key=committee_key, _signing_certificates=True).demand(
            storage.AccessError(f"Committee not found: {committee_key}", status=404)
        )
        return await self._keys_file_text(committee)

    async def _keys_file_text(self, committee: sql.Committee) -> str:
        if not committee.signing_certificates:
            raise storage.AccessError(f"No keys found for committee {committee.key} to generate KEYS file.", status=404)

        sorted_keys = sorted(committee.signing_certificates, key=lambda k: k.fingerprint)

        keys_content_list = []
        for key in sorted_keys:
            apache_uid = key.apache_uid.lower() if key.apache_uid else None
            # TODO: What if there is no email?
            email = util.email_from_uid(key.primary_declared_uid or "") or ""
            comments = []
            comments.append(f"Comment: {key.fingerprint.upper()}")
            if (apache_uid is None) or (email == f"{apache_uid}@apache.org"):
                comments.append(f"Comment: {email}")
            else:
                comments.append(f"Comment: {email} ({apache_uid})")
            comment_lines = "\n".join(comments)
            armored_key = key.ascii_armored_key
            # Use the Sequoia format
            # -----BEGIN PGP PUBLIC KEY BLOCK-----
            # Comment: C46D 6658 489D DE09 CE93  8AF8 7B6A 6401 BF99 B4A3
            # Comment: Redacted Name (CODE SIGNING KEY) <redacted@apache.org>
            #
            # [...]
            if isinstance(armored_key, bytes):
                # TODO: This should not happen, but it does
                armored_key = armored_key.decode("utf-8", errors="replace")
            armored_key = armored_key.replace("BLOCK-----", "BLOCK-----\n" + comment_lines, 1)
            keys_content_list.append(armored_key)

        key_blocks_str = "\n\n\n".join(keys_content_list) + "\n"
        key_count_for_header = len(committee.signing_certificates)

        return await self.__keys_file_format(
            committee_key=committee.key,
            key_count_for_header=key_count_for_header,
            key_blocks_str=key_blocks_str,
        )

    async def _recheck_committee_drafts(self, *committee_keys: str) -> None:
        # A KEYS change only invalidates signature checks, so we limit the re-queue to .asc files
        affected = {committee_key for committee_key in committee_keys if committee_key}
        if not affected:
            return
        drafts = await self.__data.release(
            phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            _committee=True,
        ).all()
        for draft in drafts:
            committee = draft.project.committee
            if (committee is None) or (committee.key not in affected):
                continue
            if not draft.latest_revision_number:
                continue
            await tasks.draft_checks(
                self.__asf_uid,
                draft.safe_project_key,
                draft.safe_version_key,
                draft.safe_latest_revision_number,
                suffix_filter=[".asc"],
            )

    async def _publish_keys_to_svn(
        self, committee: sql.Committee, content: str | None
    ) -> outcome.Outcome[datatypes.KeysPublish]:
        if not committee.automated_keys_file:
            return outcome.Result(datatypes.KeysPublish.AUTOMATION_DISABLED)
        if not config.get().SVN_PUBLISH_URL:
            return outcome.Result(datatypes.KeysPublish.SVN_NOT_CONFIGURED)
        target_url = util.svn_publish_internal_url(committee, None) + "/KEYS"
        temp_dir: str | None = None
        source = pathlib.Path("/dev/null")
        try:
            if content is not None:
                temp_dir = await asyncio.to_thread(tempfile.mkdtemp, dir=paths.get_tmp_dir())
                source = pathlib.Path(temp_dir) / "KEYS"
                await asyncio.to_thread(source.write_text, content, encoding="utf-8")
            await svn.publish_file(source, target_url, self.__asf_uid, f"Publish KEYS for {committee.key} via ATR")
        except svn.CommandExecutionError as e:
            log.warning(f"Failed to publish KEYS to SVN for committee {committee.key}: {e}")
            return outcome.Error(RuntimeError(svn.error_message(e)))
        except Exception as e:
            log.warning(f"Failed to publish KEYS to SVN for committee {committee.key}: {e}")
            return outcome.Error(e)
        finally:
            if temp_dir is not None:
                await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)
        return outcome.Result(datatypes.KeysPublish.PUBLISHED)

    async def _sync_committee_keys_file(self, committee_key: str) -> tuple[int, outcome.Outcome[datatypes.KeysPublish]]:
        committee = await self.__data.committee(key=committee_key, _signing_certificates=True).demand(
            storage.AccessError(f"Committee not found: {committee_key}", status=404)
        )

        if not committee.signing_certificates:
            # We use an empty file for no KEYS in SVN
            svn_outcome = await self._publish_keys_to_svn(committee, None)
            return 0, svn_outcome

        full_keys_file_content = await self._keys_file_text(committee)
        svn_outcome = await self._publish_keys_to_svn(committee, full_keys_file_content)
        return len(committee.signing_certificates), svn_outcome

    async def test_user_delete_all(self, test_uid: str) -> outcome.Outcome[int]:
        """Delete all OpenPGP keys and their links for a test user."""
        if not config.is_test_mode():
            return outcome.Error(storage.AccessError("Test key deletion not enabled", status=403))

        try:
            test_user_keys = await self.__data.signing_certificate(
                apache_uid=test_uid, deleted=db.NOT_SET, _committees=True
            ).all()

            deleted_count = 0
            deleted_fingerprints: list[str] = []
            for key in test_user_keys:
                # We must do this here otherwise SQLAlchemy does not know about the deletions
                key.committees.clear()
                await self.__data.flush()

                keylinks_query = sqlmodel.select(sql.KeyLink).where(sql.KeyLink.key_fingerprint == key.fingerprint)
                keylinks_result = await self.__data.execute(keylinks_query)
                keylinks = keylinks_result.all()
                for keylink_row in keylinks:
                    await self.__data.delete(keylink_row[0])

                await self.__data.delete(key)
                deleted_count += 1
                deleted_fingerprints.append(key.fingerprint)

            await self.__data.commit()
            if deleted_count > 0:
                self.__write_as.append_to_audit_log(
                    action="key_delete_all_for_test_user",
                    asf_uid=self.__asf_uid,
                    target_asf_uid=test_uid,
                    keys_deleted=deleted_count,
                    fingerprints=[f for f in sorted(deleted_fingerprints)],
                )
            return outcome.Result(deleted_count)
        except Exception as e:
            return outcome.Error(e)

    async def update_committee_associations(
        self,
        fingerprint: str,
        selected_committee_keys: list[str],
    ) -> datatypes.KeyAssociationUpdate:
        via = sql.validate_instrumented_attribute

        key = await self.__data.signing_certificate(
            fingerprint=fingerprint,
            apache_uid=self.__asf_uid,
            _committees=True,
        ).get()
        if not key:
            raise storage.AccessError("Key not found or not owned by you", status=404)

        old_committee_keys = {c.key for c in key.committees}
        new_committee_keys = set(selected_committee_keys)
        to_add = new_committee_keys - old_committee_keys
        to_remove = old_committee_keys - new_committee_keys
        affected = to_add | to_remove

        if not affected:
            return datatypes.KeyAssociationUpdate(added=to_add, removed=to_remove, publications={})

        for committee_key in sorted(to_add):
            self.__write.as_committee_participant(committee_key)

        await self.__data.begin_immediate()

        if to_add:
            link_values = [{"committee_key": ck, "key_fingerprint": fingerprint} for ck in to_add]
            await self.__data.execute(
                sqlite.insert(sql.KeyLink)
                .values(link_values)
                .on_conflict_do_nothing(index_elements=["committee_key", "key_fingerprint"])
            )

        if to_remove:
            await self.__data.execute(
                sqlmodel.delete(sql.KeyLink).where(
                    via(sql.KeyLink.key_fingerprint) == fingerprint,
                    via(sql.KeyLink.committee_key).in_(to_remove),
                )
            )

        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            action="key_update_committee_associations",
            asf_uid=self.__asf_uid,
            fingerprint=fingerprint,
            committees_added=[k for k in sorted(to_add)],
            committees_removed=[k for k in sorted(to_remove)],
        )

        publications: dict[str, outcome.Outcome[datatypes.KeysPublish]] = {}
        for committee_key in sorted(affected):
            _, publication = await self._sync_committee_keys_file(committee_key)
            publications[committee_key] = publication

        await self._recheck_committee_drafts(*affected)

        return datatypes.KeyAssociationUpdate(added=to_add, removed=to_remove, publications=publications)

    def __block_model(self, key_block: str, ldap_data: cache.EmailUidLookup) -> datatypes.Key:
        # This cache is only held for the session
        if key_block in self.__key_block_models_cache:
            cached_key_models = self.__key_block_models_cache[key_block]
            if len(cached_key_models) == 1:
                return cached_key_models[0]
            else:
                raise ValueError("Expected one key block, got none or multiple")

        key = self.__block_model_create(key_block, ldap_data)
        self.__key_block_models_cache[key_block] = [key]
        return key

    def __block_model_create(self, key_block: str, ldap_data: cache.EmailUidLookup) -> datatypes.Key:
        public_key, _ = openpgp.PublicKey.from_armor(key_block)
        key_model = self.public_key_model(public_key, ldap_data, original_key_block=key_block)
        _validate_key_strength(public_key)
        return datatypes.Key(
            status=datatypes.KeyStatus.PARSED,
            key_model=key_model,
            member_ids=sorted(util.openpgp_member_ids(public_key)),
        )

    async def __database_add_model(
        self,
        key: datatypes.Key,
    ) -> tuple[outcome.Outcome[datatypes.Key], dict[str, outcome.Outcome[datatypes.KeysPublish]]]:
        via = sql.validate_instrumented_attribute

        await self.__data.begin_immediate()

        key_values = [key.key_model.model_dump(exclude={"committees"})]
        key_insert_result = await self.__data.execute(
            sqlite.insert(sql.SigningCertificate)
            .values(key_values)
            .on_conflict_do_nothing(index_elements=["fingerprint"])
            .returning(via(sql.SigningCertificate.fingerprint))
        )
        inserted = key_insert_result.one_or_none() is not None
        undeleted = []
        refreshed = False
        if inserted:
            await self.__signature_hints_consume([key])
        else:
            # The fingerprint only covers the primary packet, so an existing row can hold a stale
            # block - a since-revoked, re-signed, or re-subkeyed export of the same key. Bring it up
            # to date, which also carries the apache_uid the undelete below would otherwise set
            refreshed = await self.__refresh_stored_key(key.key_model)
            undeleted = await self.__undelete_keys([key.key_model.fingerprint])
            if undeleted and not refreshed:
                await self.__data.execute(
                    sqlmodel.update(sql.SigningCertificate)
                    .where(via(sql.SigningCertificate.fingerprint) == key.key_model.fingerprint)
                    .values(apache_uid=key.key_model.apache_uid)
                )
        # A refused downgrade kept the stored block, so its rows must not be rebuilt from the block we
        # declined; only sync when the block actually changed
        if inserted or refreshed:
            await _sync_signing_keys(self.__data, [key.key_model])
        await self.__data.commit()

        if undeleted:
            self.__write_as.append_to_audit_log(
                action="key_undelete",
                asf_uid=self.__asf_uid,
                fingerprints=[f for f in undeleted],
            )
            publications = await self.__sync_committees_for_keys(undeleted)
            log.info(f"Undeleted key {key.key_model.fingerprint}")
            undeleted_key = datatypes.Key(status=datatypes.KeyStatus.INSERTED, key_model=key.key_model)
            return outcome.Result(undeleted_key), publications

        if refreshed:
            self.__write_as.append_to_audit_log(
                action="key_refresh",
                asf_uid=self.__asf_uid,
                fingerprint=key.key_model.fingerprint,
                key_apache_uid=key.key_model.apache_uid,
            )
            # A changed block can flip an existing signature check, so let its committees catch up
            publications = await self.__sync_committees_for_keys([key.key_model.fingerprint])
            log.info(f"Refreshed stored key {key.key_model.fingerprint}")
            refreshed_key = datatypes.Key(status=datatypes.KeyStatus.REFRESHED, key_model=key.key_model)
            return outcome.Result(refreshed_key), publications

        if not inserted:
            log.info(f"Key {key.key_model.fingerprint} already exists in database")
            return outcome.Result(datatypes.Key(status=datatypes.KeyStatus.PARSED, key_model=key.key_model)), {}

        log.info(f"Inserted key {key.key_model.fingerprint}")
        self.__write_as.append_to_audit_log(
            action="key_insert",
            asf_uid=self.__asf_uid,
            fingerprint=key.key_model.fingerprint,
            key_apache_uid=key.key_model.apache_uid,
        )

        # TODO: PARSED now acts as "ALREADY_ADDED"
        return outcome.Result(datatypes.Key(status=datatypes.KeyStatus.INSERTED, key_model=key.key_model)), {}

    async def __ensure_one(
        self, key_file_text: str, associate: bool = True
    ) -> tuple[outcome.Outcome[datatypes.Key], dict[str, outcome.Outcome[datatypes.KeysPublish]]]:
        try:
            key_blocks = util.parse_key_blocks(key_file_text)
        except Exception as e:
            return outcome.Error(e), {}
        if len(key_blocks) != 1:
            return outcome.Error(ValueError("Expected one key block, got none or multiple")), {}
        key_block = key_blocks[0]
        try:
            key = await asyncio.to_thread(self.__block_model_create, key_block, cache.EmailUidLookup({}))
            if key.key_model.apache_uid is None:
                ldap_data = await cache.email_uid_view_or_live()
                key = await asyncio.to_thread(self.__block_model, key_block, ldap_data)
            else:
                self.__key_block_models_cache[key_block] = [key]
        except Exception as e:
            return outcome.Error(e), {}
        if key.key_model.apache_uid is None:
            return outcome.Error(
                datatypes.UnknownApacheUidError(
                    "OpenPGP key could not be associated with an ASF UID. Import it through a KEYS file instead."
                )
            ), {}
        return await self.__database_add_model(key)

    async def __keys_file_format(
        self,
        committee_key: str,
        key_count_for_header: int,
        key_blocks_str: str,
    ) -> str:
        timestamp_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
        purpose_text = f"""\
This file contains the {key_count_for_header} OpenPGP public keys used by \
committers of the Apache {committee_key} projects to sign official \
release artifacts. Verifying the signature on a downloaded artifact using one \
of the keys in this file provides confidence that the artifact is authentic \
and was published by the committee.\
"""
        wrapped_purpose = "\n".join(
            textwrap.wrap(
                purpose_text,
                width=62,
                initial_indent="# ",
                subsequent_indent="# ",
                break_long_words=False,
                replace_whitespace=False,
            )
        )

        header_content = f"""\
# Apache Software Foundation (ASF)
# Signing keys for the {committee_key} committee
# Generated at {timestamp_str} UTC
#
{wrapped_purpose}
#
# 1. Import these keys into your GPG keyring:
#    gpg --import KEYS
#
# 2. Verify the signature file against the release artifact:
#    gpg --verify "${{ARTIFACT}}.asc" "${{ARTIFACT}}"
#
# For details on Apache release signing and verification, see:
# https://infra.apache.org/release-signing.html


"""

        full_keys_file_content = header_content + key_blocks_str
        return full_keys_file_content

    async def __refresh_stored_key(self, model: sql.SigningCertificate) -> bool:
        existing = await self.__data.signing_certificate(fingerprint=model.fingerprint, deleted=db.NOT_SET).get()
        if (existing is None) or (existing.ascii_armored_key == model.ascii_armored_key):
            return False
        downgrade = _block_downgrade_reason(existing.ascii_armored_key, model.ascii_armored_key)
        if downgrade is not None:
            log.warning(f"Not refreshing {model.fingerprint}: the uploaded block {downgrade}")
            return False
        existing.ascii_armored_key = model.ascii_armored_key
        existing.latest_self_signature = model.latest_self_signature
        existing.primary_declared_uid = model.primary_declared_uid
        existing.secondary_declared_uids = model.secondary_declared_uids
        existing.apache_uid = model.apache_uid
        self.__data.add(existing)
        return True

    async def __signature_hints_consume(self, keys: list[datatypes.Key]) -> list[str]:
        via = sql.validate_instrumented_attribute
        member_map = {key.key_model.fingerprint: set(key.member_ids) for key in keys}
        all_ids = sorted({member_id for member_ids in member_map.values() for member_id in member_ids})
        if not all_ids:
            return []
        hint_rows = await self.__data.execute(
            sqlmodel.select(via(sql.SignatureHint.hint)).where(via(sql.SignatureHint.hint).in_(all_ids))
        )
        matched = set(hint_rows.scalars().all())
        if not matched:
            return []
        flagged = sorted(fingerprint for fingerprint, member_ids in member_map.items() if member_ids & matched)
        await self.__data.execute(
            sqlmodel.update(sql.SigningCertificate)
            .where(via(sql.SigningCertificate.fingerprint).in_(flagged))
            .values(historic_use=True)
        )
        await self.__data.execute(
            sqlmodel.delete(sql.SignatureHint).where(via(sql.SignatureHint.hint).in_(sorted(matched)))
        )
        return flagged

    async def __sync_committees_for_keys(
        self, fingerprints: list[str]
    ) -> dict[str, outcome.Outcome[datatypes.KeysPublish]]:
        if not fingerprints:
            return {}
        via = sql.validate_instrumented_attribute
        link_rows = await self.__data.execute(
            sqlmodel.select(via(sql.KeyLink.committee_key))
            .where(via(sql.KeyLink.key_fingerprint).in_(fingerprints))
            .distinct()
        )
        committee_keys = sorted(set(link_rows.scalars().all()))
        publications: dict[str, outcome.Outcome[datatypes.KeysPublish]] = {}
        for committee_key in committee_keys:
            _, publication = await self._sync_committee_keys_file(committee_key)
            publications[committee_key] = publication
        await self._recheck_committee_drafts(*committee_keys)
        return publications

    def __uids_asf_uid(self, uids: list[str], ldap_data: cache.EmailUidLookup) -> str | None:
        # Test data
        test_key_uids = [
            "Apache Tooling (For test use only) <apache-tooling@example.invalid>",
        ]

        if uids == test_key_uids:
            # Allow the test key
            if config.is_test_mode() and (self.__asf_uid == "test"):
                # TODO: "test" is already an admin user
                # But we want to narrow that down to only actions like this
                # TODO: Add include_test: bool to user.is_admin?
                return "test"
            if user.is_admin(self.__asf_uid):
                return self.__asf_uid

        # Regular data
        emails = []
        for uid in uids:
            # This returns a lower case email address, whatever the case of the input
            if email := util.email_from_uid(uid):
                if email.endswith("@apache.org"):
                    return email.removesuffix("@apache.org")
                emails.append(email)
        # We did not find a direct @apache.org email address
        # Therefore, search cached LDAP data
        for email in emails:
            if email in ldap_data:
                return ldap_data[email]
        return None

    async def __undelete_keys(self, fingerprints: list[str]) -> list[str]:
        if not fingerprints:
            return []
        via = sql.validate_instrumented_attribute
        result = await self.__data.execute(
            sqlmodel.update(sql.SigningCertificate)
            .where(
                via(sql.SigningCertificate.fingerprint).in_(sorted(fingerprints)),
                via(sql.SigningCertificate.deleted).is_not(None),
            )
            .values(deleted=None)
            .returning(via(sql.SigningCertificate.fingerprint))
        )
        return sorted(result.scalars().all())


class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    async def associate_fingerprint(self, fingerprint: str) -> outcome.Outcome[datatypes.LinkedCommittee]:
        via = sql.validate_instrumented_attribute
        link_values = [{"committee_key": self.__committee_key, "key_fingerprint": fingerprint}]
        try:
            await self.__data.signing_certificate(fingerprint=fingerprint).demand(
                storage.AccessError(f"Key not found: {fingerprint}", status=404)
            )
            link_insert_result = await self.__data.execute(
                sqlite.insert(sql.KeyLink)
                .values(link_values)
                .on_conflict_do_nothing(index_elements=["committee_key", "key_fingerprint"])
                .returning(via(sql.KeyLink.key_fingerprint))
            )
            link_inserted = link_insert_result.one_or_none() is not None
            if not link_inserted:
                # e = storage.AccessError(f"Key not found: {fingerprint}")
                # return storage.OutcomeException(e)
                pass
            await self.__data.commit()
        except Exception as e:
            return outcome.Error(e)
        if link_inserted:
            self.__write_as.append_to_audit_log(
                action="key_associate_committee",
                asf_uid=self.__asf_uid,
                fingerprint=fingerprint,
                committee_key=self.__committee_key,
            )
            await self._recheck_committee_drafts(self.__committee_key)
        try:
            autogenerated_outcome, publication = await self.autogenerate_keys_file()
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(
            datatypes.LinkedCommittee(
                name=self.__committee_key,
                autogenerated_keys_file=autogenerated_outcome,
                publication=publication,
            )
        )

    async def autogenerate_keys_file(
        self,
    ) -> tuple[outcome.Outcome[int], outcome.Outcome[datatypes.KeysPublish]]:
        no_keys = storage.AccessError(
            f"No keys found for committee {self.__committee_key} to generate KEYS file.", status=404
        )
        try:
            committee = await self.committee()
            if not committee.signing_certificates:
                return outcome.Error(no_keys), outcome.Error(no_keys)
            key_count, svn_outcome = await self._sync_committee_keys_file(self.__committee_key)
        except Exception as e:
            return outcome.Error(e), outcome.Error(e)
        if key_count == 0:
            return outcome.Error(no_keys), svn_outcome
        return outcome.Result(key_count), svn_outcome

    async def committee(self) -> sql.Committee:
        return await self.__data.committee(key=self.__committee_key, _signing_certificates=True).demand(
            storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
        )

    @property
    def committee_key(self) -> str:
        return self.__committee_key

    async def ensure_associated(
        self, keys_file_text: str
    ) -> tuple[outcome.List[datatypes.Key], dict[str, outcome.Outcome[datatypes.KeysPublish]]]:
        outcomes, publications = await self.__ensure(keys_file_text, associate=True)
        if not outcomes.any_result:
            return outcomes, publications
        _, publication = await self.autogenerate_keys_file()
        publications[self.__committee_key] = publication
        return outcomes, publications

    async def ensure_stored(
        self, keys_file_text: str
    ) -> tuple[outcome.List[datatypes.Key], dict[str, outcome.Outcome[datatypes.KeysPublish]]]:
        outcomes, publications = await self.__ensure(keys_file_text, associate=False)
        if not outcomes.any_result:
            return outcomes, publications
        _, publication = await self.autogenerate_keys_file()
        publications[self.__committee_key] = publication
        return outcomes, publications

    async def import_keys_file(
        self, project_key: safe.ProjectKey, version_key: safe.VersionKey
    ) -> tuple[outcome.List[datatypes.Key], dict[str, outcome.Outcome[datatypes.KeysPublish]]]:
        release = await self.__data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
        ).demand(storage.AccessError(f"Release not found: {project_key} {version_key}", status=404))
        keys_path = paths.release_directory(release) / "KEYS"
        async with aiofiles.open(keys_path, encoding="utf-8") as f:
            keys_file_text = await f.read()
        if release.committee is None:
            raise storage.AccessError("No committee found for release - Invalid state", status=500)
        if release.committee.key != self.__committee_key:
            raise storage.AccessError(
                f"Release {project_key!s} {version_key!s} is not associated with committee {self.__committee_key}",
                status=403,
            )

        outcomes, publications = await self.ensure_associated(keys_file_text)
        release_keys_removed = False
        # Remove the KEYS file if 100% imported
        if (outcomes.result_count > 0) and (outcomes.error_count == 0):
            description = "Removed KEYS file after successful import through web interface"

            async def modify(path: safe.StatePath, _old_rev: sql.Revision | None) -> None:
                path_in_new_revision = path / "KEYS"
                await aiofiles.os.remove(path_in_new_revision)

            await self.__write_as.revision.create_revision_with_quarantine(
                project_key,
                version_key,
                self.__asf_uid,
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                description=description,
                modify=modify,
            )
            release_keys_removed = True
        self.__write_as.append_to_audit_log(
            action="key_import",
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            project_key=str(project_key),
            version_key=str(version_key),
            imported_keys=outcomes.result_count,
            failed_keys=outcomes.error_count,
            release_keys_removed=release_keys_removed,
        )
        return outcomes, publications

    def __block_models(self, key_block: str, ldap_data: cache.EmailUidLookup) -> list[datatypes.Key | Exception]:
        try:
            public_key, _ = openpgp.PublicKey.from_armor(key_block)
        except Exception as e:
            raise ValueError(f"Error loading OpenPGP key block: {e}") from e
        key_list = []
        try:
            key_model = self.public_key_model(public_key, ldap_data, original_key_block=key_block)
            _validate_key_strength(public_key)
            key = datatypes.Key(
                status=datatypes.KeyStatus.PARSED,
                key_model=key_model,
                member_ids=sorted(util.openpgp_member_ids(public_key)),
            )
            key_list.append(key)
        except Exception as e:
            key_list.append(e)
        return key_list

    async def __database_add_models(
        self, outcomes: outcome.List[datatypes.Key], associate: bool = True
    ) -> tuple[outcome.List[datatypes.Key], dict[str, outcome.Outcome[datatypes.KeysPublish]]]:
        # Try to upsert all models and link to the committee in one transaction
        publications: dict[str, outcome.Outcome[datatypes.KeysPublish]] = {}
        try:
            outcomes, publications = await self.__database_add_models_core(outcomes, associate=associate)
        except Exception as e:
            # This logging is just so that ruff does not erase e
            log.info(f"Post-parse error: {e}")

            def raise_post_parse_error(key: datatypes.Key) -> NoReturn:
                nonlocal e
                # We assume here that the transaction was rolled back correctly
                key = datatypes.Key(status=datatypes.KeyStatus.PARSED, key_model=key.key_model)
                raise datatypes.PublicKeyError(key, e)

            outcomes.update_roes(Exception, raise_post_parse_error)
        return outcomes, publications

    async def __database_add_models_core(  # noqa: C901
        self,
        outcomes: outcome.List[datatypes.Key],
        associate: bool = True,
    ) -> tuple[outcome.List[datatypes.Key], dict[str, outcome.Outcome[datatypes.KeysPublish]]]:
        via = sql.validate_instrumented_attribute
        key_list = outcomes.results()

        await self.__data.begin_immediate()
        committee = await self.committee()

        key_values = [key.key_model.model_dump(exclude={"committees"}) for key in key_list]
        incoming_blocks = {v["fingerprint"]: v["ascii_armored_key"] for v in key_values}
        stored_blocks: dict[str, str] = {}
        if incoming_blocks:
            stored_result = await self.__data.execute(
                sqlmodel.select(
                    via(sql.SigningCertificate.fingerprint),
                    via(sql.SigningCertificate.ascii_armored_key),
                ).where(via(sql.SigningCertificate.fingerprint).in_(sorted(incoming_blocks)))
            )
            stored_blocks = {fingerprint: block for fingerprint, block in stored_result.all()}
        # A key we already hold whose incoming block differs has been re-imported with a newer state,
        # which can flip an existing signature check even when nothing about the association changed
        refreshed_fingerprints = {
            fingerprint
            for fingerprint, block in incoming_blocks.items()
            if (fingerprint in stored_blocks) and (stored_blocks[fingerprint] != block)
        }
        # A re-import must not roll a held key's effective state back, so leave any block which would do
        # so untouched rather than overwrite the stored one
        downgraded = {
            fingerprint
            for fingerprint in refreshed_fingerprints
            if _block_downgrade_reason(stored_blocks[fingerprint], incoming_blocks[fingerprint]) is not None
        }
        if downgraded:
            kept = ", ".join(sorted(downgraded))
            log.warning(f"Keeping the stored block for {util.plural(len(downgraded), 'key')}: {kept}")
            key_list = [key for key in key_list if key.key_model.fingerprint not in downgraded]
            key_values = [values for values in key_values if values["fingerprint"] not in downgraded]
        if key_values:
            stmt = sqlite.insert(sql.SigningCertificate).values(key_values)
            # The fingerprint only covers the primary packet, so a re-import can carry a newer block
            # for a key we already hold - a revocation, an extended expiry, a fresh subkey. Refresh
            # the whole derived row, not just the apache_uid
            stmt = stmt.on_conflict_do_update(
                index_elements=["fingerprint"],
                set_={
                    "apache_uid": stmt.excluded.apache_uid,
                    "ascii_armored_key": stmt.excluded.ascii_armored_key,
                    "latest_self_signature": stmt.excluded.latest_self_signature,
                    "primary_declared_uid": stmt.excluded.primary_declared_uid,
                    "secondary_declared_uids": stmt.excluded.secondary_declared_uids,
                },
            )
            key_insert_result = await self.__data.execute(
                stmt.returning(via(sql.SigningCertificate.fingerprint)),
            )
            key_inserts = {row.fingerprint for row in key_insert_result}
            log.info(f"Inserted or updated {util.plural(len(key_inserts), 'key')}")
        else:
            # TODO: Warn the user about any keys that were already inserted
            key_inserts = set()
            log.info("Inserted 0 keys (no keys to insert)")

        def replace_with_inserted(key: datatypes.Key) -> datatypes.Key:
            if key.key_model.fingerprint in key_inserts:
                key.status = datatypes.KeyStatus.INSERTED
            return key

        outcomes.update_roes(Exception, replace_with_inserted)

        persisted_fingerprints = {v["fingerprint"] for v in key_values}
        # An artifact points at the SigningKey which signed it, so these rows must exist before an
        # artifact can be attributed
        await _sync_signing_keys(self.__data, [key.key_model for key in key_list])
        undeleted = await self.__undelete_keys(sorted(persisted_fingerprints))
        await self.__signature_hints_consume(key_list)
        await self.__data.flush()

        existing_fingerprints = {k.fingerprint for k in committee.signing_certificates}
        new_fingerprints = persisted_fingerprints - existing_fingerprints
        link_inserts = set()
        if new_fingerprints and associate:
            link_values = [{"committee_key": self.__committee_key, "key_fingerprint": fp} for fp in new_fingerprints]
            link_insert_result = await self.__data.execute(
                sqlite.insert(sql.KeyLink)
                .values(link_values)
                .on_conflict_do_nothing(index_elements=["committee_key", "key_fingerprint"])
                .returning(via(sql.KeyLink.key_fingerprint))
            )
            link_inserts = {row.key_fingerprint for row in link_insert_result}
            log.info(f"Inserted {util.plural(len(link_inserts), 'key link')}")

            def replace_with_linked(key: datatypes.Key) -> datatypes.Key:
                # nonlocal link_inserts
                match key:
                    case datatypes.Key(status=datatypes.KeyStatus.INSERTED):
                        if key.key_model.fingerprint in link_inserts:
                            key.status = datatypes.KeyStatus.INSERTED_AND_LINKED
                    case datatypes.Key(status=datatypes.KeyStatus.PARSED):
                        if key.key_model.fingerprint in link_inserts:
                            key.status = datatypes.KeyStatus.LINKED
                return key

            outcomes.update_roes(Exception, replace_with_linked)
        else:
            log.info("Inserted 0 key links (none to insert)")

        await self.__data.commit()
        if key_inserts or link_inserts:
            self.__write_as.append_to_audit_log(
                action="key_insert_or_associate_bulk",
                asf_uid=self.__asf_uid,
                committee_key=self.__committee_key,
                inserted_fingerprints=sorted(key_inserts),
                linked_fingerprints=sorted(link_inserts),
            )
        publications: dict[str, outcome.Outcome[datatypes.KeysPublish]] = {}
        if undeleted:
            self.__write_as.append_to_audit_log(
                action="key_undelete",
                asf_uid=self.__asf_uid,
                fingerprints=[f for f in undeleted],
            )
            publications = await self.__sync_committees_for_keys(undeleted)
        if link_inserts or (refreshed_fingerprints - downgraded):
            await self._recheck_committee_drafts(self.__committee_key)
        return outcomes, publications

    async def __ensure(
        self,
        keys_file_text: str,
        associate: bool = True,
    ) -> tuple[outcome.List[datatypes.Key], dict[str, outcome.Outcome[datatypes.KeysPublish]]]:
        outcomes = outcome.List[datatypes.Key]()
        try:
            ldap_data = await cache.email_uid_view_or_live()
            key_blocks = util.parse_key_blocks(keys_file_text)
        except Exception as e:
            outcomes.append_error(e)
            return outcomes, {}
        # TODO: Change self.__block_models to return outcomes
        tasks = [
            asyncio.create_task(asyncio.to_thread(self.__block_models, key_block, ldap_data))
            for key_block in key_blocks
        ]
        key_model_batches = await asyncio.gather(*tasks, return_exceptions=True)
        for key_model_batch in key_model_batches:
            if isinstance(key_model_batch, Exception):
                outcomes.append_error(key_model_batch)
            elif isinstance(key_model_batch, BaseException):
                raise key_model_batch
            else:
                outcomes.extend_roes(Exception, key_model_batch)
        # Try adding the keys to the database
        # If not, all keys will be replaced with a PostParseError
        return await self.__database_add_models(outcomes, associate=associate)

    async def __signature_hints_consume(self, keys: list[datatypes.Key]) -> list[str]:
        via = sql.validate_instrumented_attribute
        member_map = {key.key_model.fingerprint: set(key.member_ids) for key in keys}
        all_ids = sorted({member_id for member_ids in member_map.values() for member_id in member_ids})
        if not all_ids:
            return []
        hint_rows = await self.__data.execute(
            sqlmodel.select(via(sql.SignatureHint.hint)).where(via(sql.SignatureHint.hint).in_(all_ids))
        )
        matched = set(hint_rows.scalars().all())
        if not matched:
            return []
        flagged = sorted(fingerprint for fingerprint, member_ids in member_map.items() if member_ids & matched)
        await self.__data.execute(
            sqlmodel.update(sql.SigningCertificate)
            .where(via(sql.SigningCertificate.fingerprint).in_(flagged))
            .values(historic_use=True)
        )
        await self.__data.execute(
            sqlmodel.delete(sql.SignatureHint).where(via(sql.SignatureHint.hint).in_(sorted(matched)))
        )
        return flagged

    async def __sync_committees_for_keys(
        self, fingerprints: list[str]
    ) -> dict[str, outcome.Outcome[datatypes.KeysPublish]]:
        if not fingerprints:
            return {}
        via = sql.validate_instrumented_attribute
        link_rows = await self.__data.execute(
            sqlmodel.select(via(sql.KeyLink.committee_key))
            .where(via(sql.KeyLink.key_fingerprint).in_(fingerprints))
            .distinct()
        )
        committee_keys = sorted(set(link_rows.scalars().all()))
        publications: dict[str, outcome.Outcome[datatypes.KeysPublish]] = {}
        for committee_key in committee_keys:
            _, publication = await self._sync_committee_keys_file(committee_key)
            publications[committee_key] = publication
        await self._recheck_committee_drafts(*committee_keys)
        return publications

    async def __undelete_keys(self, fingerprints: list[str]) -> list[str]:
        if not fingerprints:
            return []
        via = sql.validate_instrumented_attribute
        result = await self.__data.execute(
            sqlmodel.update(sql.SigningCertificate)
            .where(
                via(sql.SigningCertificate.fingerprint).in_(sorted(fingerprints)),
                via(sql.SigningCertificate.deleted).is_not(None),
            )
            .values(deleted=None)
            .returning(via(sql.SigningCertificate.fingerprint))
        )
        return sorted(result.scalars().all())


class CommitteeMember(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data, committee_key)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    async def set_automated_keys_file(self, enabled: bool) -> bool:
        committee = await self.__data.committee(key=self.__committee_key).demand(
            storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
        )
        if committee.automated_keys_file == enabled:
            return False
        committee.automated_keys_file = enabled
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            automated_keys_file=enabled,
        )
        return True


class FoundationAdmin(CommitteeMember):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeAdmin,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data, committee_key)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    @property
    def committee_key(self) -> str:
        return self.__committee_key

    async def delete_committee_keys(self) -> tuple[int, int, outcome.Outcome[datatypes.KeysPublish] | None]:
        via = sql.validate_instrumented_attribute
        await self.__data.committee(key=self.__committee_key).demand(
            storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
        )

        await self.__data.begin_immediate()
        link_rows = await self.__data.execute(
            sqlmodel.select(via(sql.KeyLink.key_fingerprint)).where(
                via(sql.KeyLink.committee_key) == self.__committee_key
            )
        )
        unlinked = sorted(set(link_rows.scalars().all()))
        if not unlinked:
            await self.__data.commit()
            return (0, 0, None)

        num_unlinked = len(unlinked)
        fingerprints: list[basic.JSON] = [fingerprint for fingerprint in unlinked]
        await self.__data.execute(
            sqlmodel.delete(sql.KeyLink).where(via(sql.KeyLink.committee_key) == self.__committee_key)
        )
        still_linked_rows = await self.__data.execute(
            sqlmodel.select(via(sql.KeyLink.key_fingerprint)).where(via(sql.KeyLink.key_fingerprint).in_(unlinked))
        )
        still_linked = set(still_linked_rows.scalars().all())
        orphaned = [fingerprint for fingerprint in unlinked if fingerprint not in still_linked]

        num_deleted = 0
        if orphaned:
            update_result = await self.__data.execute(
                sqlmodel.update(sql.SigningCertificate)
                .where(
                    via(sql.SigningCertificate.fingerprint).in_(orphaned),
                    via(sql.SigningCertificate.deleted).is_(None),
                )
                .values(deleted=datetime.datetime.now(datetime.UTC))
            )
            num_deleted = getattr(update_result, "rowcount", 0)

        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            keys_unlinked=num_unlinked,
            keys_deleted=num_deleted,
            fingerprints=fingerprints,
        )
        try:
            _, publication = await self._sync_committee_keys_file(self.__committee_key)
        except Exception as e:
            self.__write_as.append_to_audit_log(
                action="delete_committee_keys_sync_failed",
                asf_uid=self.__asf_uid,
                committee_key=self.__committee_key,
                keys_unlinked=num_unlinked,
                keys_deleted=num_deleted,
                fingerprints=fingerprints,
                error=str(e),
            )
            raise

        await self._recheck_committee_drafts(self.__committee_key)
        return (num_unlinked, num_deleted, publication)


def _validate_key_strength(key: openpgp.PublicKey) -> None:
    """Raise ValueError if the key is recently-generated and does not meet the minimum cryptographic strength."""
    algorithm = _algorithm_id(key.public_key_algorithm)
    length = _key_length(key)
    created = datetime.datetime.fromtimestamp(key.created_at, tz=datetime.UTC)
    if created > datetime.datetime(2026, 4, 1, 0, 0, 0, tzinfo=datetime.UTC):
        if algorithm not in _APPROVED_KEY_ALGORITHMS:
            raise ValueError(
                f"Key algorithm {_algorithm_name(algorithm)} is not accepted for upload; use RSA, ECDSA, or EdDSA"
            )
        if (algorithm in _RSA_ALGORITHMS) and (length < _RSA_MINIMUM_BITS):
            raise ValueError(
                f"RSA key size {length} bits is below the minimum of {_RSA_MINIMUM_BITS} bits required by Apache policy"
            )
        if (algorithm not in _RSA_ALGORITHMS) and (length < _EC_MINIMUM_BITS):
            raise ValueError(f"Elliptic curve key size {length} bits is below the minimum of {_EC_MINIMUM_BITS} bits")
