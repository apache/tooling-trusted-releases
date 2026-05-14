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
import textwrap
from typing import Final, NoReturn

import aiofiles
import aiofiles.os
import openpgp
import sqlalchemy.dialects.sqlite as sqlite
import sqlalchemy.orm as orm
import sqlmodel

import atr.cache as cache
import atr.config as config
import atr.db as db
import atr.log as log
import atr.models.basic as basic
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.storage.types as types
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


def _binding_self_signatures(key: openpgp.PublicKey) -> list[openpgp.SignatureInfo]:
    self_fingerprint = key.fingerprint.lower()
    self_key_id = key.key_id.lower()
    primary_bindings = [binding for binding in key.user_bindings() if binding.is_primary]
    chosen_bindings = primary_bindings or list(key.user_bindings())
    binding_sigs: list[openpgp.SignatureInfo] = []
    for binding in chosen_bindings:
        binding_sigs.extend(
            signature
            for signature in binding.signatures
            if _signature_is_self(signature, self_fingerprint, self_key_id)
        )
    return binding_sigs


def _direct_self_signatures(key: openpgp.PublicKey) -> list[openpgp.SignatureInfo]:
    self_fingerprint = key.fingerprint.lower()
    self_key_id = key.key_id.lower()
    return [
        signature
        for signature in key.direct_signature_infos()
        if _signature_is_self(signature, self_fingerprint, self_key_id)
    ]


def _effective_key_expiration_self_signature(key: openpgp.PublicKey) -> openpgp.SignatureInfo | None:
    direct_sigs = _direct_self_signatures(key)
    binding_sigs = _binding_self_signatures(key)
    if key.version >= 6:
        return _latest_signature(direct_sigs)
    if binding_sigs:
        return _latest_signature(binding_sigs)
    return _latest_signature(direct_sigs)


def _key_length(key: openpgp.PublicKey) -> int:
    public_params = key.public_params
    rsa_bits = public_params.rsa_bits
    if isinstance(rsa_bits, int):
        return rsa_bits
    dsa_bits = public_params.dsa_bits
    if isinstance(dsa_bits, int):
        return dsa_bits
    curve_bits = public_params.curve_bits
    if isinstance(curve_bits, int):
        return curve_bits
    raise ValueError(
        f"Key size is not available for algorithm {key.public_key_algorithm}:"
        f" rsa_bits={rsa_bits!r}, dsa_bits={dsa_bits!r}, curve_bits={curve_bits!r}"
    )


def _key_expires_at(key: openpgp.PublicKey) -> datetime.datetime | None:
    effective = _effective_key_expiration_self_signature(key)
    if effective is None:
        return None
    key_expiration_seconds = effective.key_expiration_seconds
    if key_expiration_seconds is None:
        return None
    return datetime.datetime.fromtimestamp(key.created_at + key_expiration_seconds, tz=datetime.UTC)


def _latest_self_signature(key: openpgp.PublicKey) -> openpgp.SignatureInfo | None:
    signatures = _direct_self_signatures(key)
    signatures.extend(_binding_self_signatures(key))
    return _latest_signature(signatures)


def _latest_self_signature_created_at(key: openpgp.PublicKey) -> datetime.datetime | None:
    signature = _latest_self_signature(key)
    if signature is None or signature.creation_time is None:
        return None
    return datetime.datetime.fromtimestamp(signature.creation_time, tz=datetime.UTC)


def _latest_signature(signatures: list[openpgp.SignatureInfo]) -> openpgp.SignatureInfo | None:
    if not signatures:
        return None
    return max(signatures, key=lambda signature: signature.creation_time or 0)


def _signature_is_self(signature: openpgp.SignatureInfo, self_fingerprint: str, self_key_id: str) -> bool:
    fingerprints = {fingerprint.lower() for fingerprint in signature.issuer_fingerprints}
    key_ids = {key_id.lower() for key_id in signature.issuer_key_ids}
    return (self_fingerprint in fingerprints) or (self_key_id in key_ids)


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

    async def delete_key(self, fingerprint: str) -> outcome.Outcome[sql.PublicSigningKey]:
        try:
            key = await self.__data.public_signing_key(
                fingerprint=fingerprint,
                apache_uid=self.__asf_uid,
                _committees=True,
            ).demand(storage.AccessError(f"Key not found: {fingerprint}", status=404))
            affected_committee_keys = {committee.key for committee in key.committees}
            await self.__data.delete(key)
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                action="key_delete",
                asf_uid=self.__asf_uid,
                fingerprint=key.fingerprint,
                committee_keys=[k for k in sorted(affected_committee_keys)],
            )
            for committee_key in sorted(affected_committee_keys):
                await self._sync_committee_keys_file(committee_key)
            return outcome.Result(key)
        except Exception as e:
            return outcome.Error(e)

    async def ensure_stored_one(self, key_file_text: str) -> outcome.Outcome[types.Key]:
        return await self.__ensure_one(key_file_text, associate=False)

    def public_key_model(
        self,
        key: openpgp.PublicKey,
        ldap_data: cache.EmailUidLookup,
        original_key_block: str | None = None,
    ) -> sql.PublicSigningKey:
        uids = list(key.user_ids)
        asf_uid = self.__uids_asf_uid(uids, ldap_data)
        if not uids:
            raise ValueError("No UIDs found in key")

        # Use the original key block if available
        ascii_armored = original_key_block if original_key_block else key.to_armored()
        expires_at = _key_expires_at(key)

        return sql.PublicSigningKey(
            fingerprint=key.fingerprint.lower(),
            algorithm=_algorithm_id(key.public_key_algorithm),
            length=_key_length(key),
            created=datetime.datetime.fromtimestamp(key.created_at, tz=datetime.UTC),
            latest_self_signature=_latest_self_signature_created_at(key),
            expires=expires_at,
            primary_declared_uid=uids[0],
            secondary_declared_uids=uids[1:],
            apache_uid=asf_uid,
            ascii_armored_key=ascii_armored,
        )

    async def keys_file_text(self, committee_key: str) -> str:
        committee = await self.__data.committee(key=committee_key, _public_signing_keys=True).demand(
            storage.AccessError(f"Committee not found: {committee_key}", status=404)
        )
        return await self._keys_file_text(committee)

    async def _keys_file_text(self, committee: sql.Committee) -> str:
        if not committee.public_signing_keys:
            raise storage.AccessError(f"No keys found for committee {committee.key} to generate KEYS file.", status=404)

        sorted_keys = sorted(committee.public_signing_keys, key=lambda k: k.fingerprint)

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
        key_count_for_header = len(committee.public_signing_keys)

        return await self.__keys_file_format(
            committee_key=committee.key,
            key_count_for_header=key_count_for_header,
            key_blocks_str=key_blocks_str,
        )

    def _committee_keys_path(self, committee: sql.Committee) -> safe.StatePath:
        return paths.committee_downloads_dir(committee) / "KEYS"

    async def _sync_committee_keys_file(self, committee_key: str) -> str | None:
        committee = await self.__data.committee(key=committee_key, _public_signing_keys=True).demand(
            storage.AccessError(f"Committee not found: {committee_key}", status=404)
        )
        committee_keys_path = self._committee_keys_path(committee)
        committee_keys_dir = committee_keys_path.parent

        if not committee.public_signing_keys:
            try:
                if await aiofiles.os.path.exists(committee_keys_path):
                    await aiofiles.os.remove(committee_keys_path)
            except OSError as e:
                raise storage.AccessError(
                    f"Failed to remove KEYS file for committee {committee_key}: {e}", status=500
                ) from e
            return None

        full_keys_file_content = await self._keys_file_text(committee)
        try:
            await aiofiles.os.makedirs(committee_keys_dir, exist_ok=True)
            await asyncio.to_thread(util.chmod_directories, committee_keys_dir, permissions=0o755)
            await asyncio.to_thread(committee_keys_path.path.write_text, full_keys_file_content, encoding="utf-8")
        except OSError as e:
            raise storage.AccessError(
                f"Failed to write KEYS file for committee {committee_key}: {e}", status=500
            ) from e
        except Exception as e:
            log.exception(f"An unexpected error occurred writing KEYS for committee {committee_key}: {e}")
            raise storage.AccessError(
                f"An unexpected error occurred writing KEYS for committee {committee_key}: {e}",
                status=500,
            ) from e
        return str(committee_keys_path)

    async def test_user_delete_all(self, test_uid: str) -> outcome.Outcome[int]:
        """Delete all OpenPGP keys and their links for a test user."""
        if not config.is_test_mode():
            return outcome.Error(storage.AccessError("Test key deletion not enabled", status=403))

        try:
            test_user_keys = await self.__data.public_signing_key(apache_uid=test_uid, _committees=True).all()

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
    ) -> set[str]:
        via = sql.validate_instrumented_attribute

        key = await self.__data.public_signing_key(
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
            return affected

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

        for committee_key in sorted(affected):
            await self._sync_committee_keys_file(committee_key)

        return affected

    def __block_model(self, key_block: str, ldap_data: cache.EmailUidLookup) -> types.Key:
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

    def __block_model_create(self, key_block: str, ldap_data: cache.EmailUidLookup) -> types.Key:
        public_key, _ = openpgp.PublicKey.from_armor(key_block)
        key_model = self.public_key_model(public_key, ldap_data, original_key_block=key_block)
        _validate_key_strength(key_model.algorithm, key_model.length, key_model.created)
        return types.Key(status=types.KeyStatus.PARSED, key_model=key_model)

    async def __database_add_model(
        self,
        key: types.Key,
    ) -> outcome.Outcome[types.Key]:
        via = sql.validate_instrumented_attribute

        await self.__data.begin_immediate()

        key_values = [key.key_model.model_dump(exclude={"committees"})]
        key_insert_result = await self.__data.execute(
            sqlite.insert(sql.PublicSigningKey)
            .values(key_values)
            .on_conflict_do_nothing(index_elements=["fingerprint"])
            .returning(via(sql.PublicSigningKey.fingerprint))
        )
        await self.__data.commit()

        if key_insert_result.one_or_none() is None:
            log.info(f"Key {key.key_model.fingerprint} already exists in database")
            return outcome.Result(types.Key(status=types.KeyStatus.PARSED, key_model=key.key_model))

        log.info(f"Inserted key {key.key_model.fingerprint}")
        self.__write_as.append_to_audit_log(
            action="key_insert",
            asf_uid=self.__asf_uid,
            fingerprint=key.key_model.fingerprint,
            key_apache_uid=key.key_model.apache_uid,
        )

        # TODO: PARSED now acts as "ALREADY_ADDED"
        return outcome.Result(types.Key(status=types.KeyStatus.INSERTED, key_model=key.key_model))

    async def __ensure_one(self, key_file_text: str, associate: bool = True) -> outcome.Outcome[types.Key]:
        try:
            key_blocks = util.parse_key_blocks(key_file_text)
        except Exception as e:
            return outcome.Error(e)
        if len(key_blocks) != 1:
            return outcome.Error(ValueError("Expected one key block, got none or multiple"))
        key_block = key_blocks[0]
        try:
            key = await asyncio.to_thread(self.__block_model_create, key_block, cache.EmailUidLookup({}))
            if key.key_model.apache_uid is None:
                ldap_data = await cache.email_uid_view_or_live()
                key = await asyncio.to_thread(self.__block_model, key_block, ldap_data)
            else:
                self.__key_block_models_cache[key_block] = [key]
        except Exception as e:
            return outcome.Error(e)
        if key.key_model.apache_uid is None:
            return outcome.Error(
                types.UnknownApacheUidError(
                    "OpenPGP key could not be associated with an ASF UID. Import it through a KEYS file instead."
                )
            )
        oc = await self.__database_add_model(key)
        return oc

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

    async def associate_fingerprint(self, fingerprint: str) -> outcome.Outcome[types.LinkedCommittee]:
        via = sql.validate_instrumented_attribute
        link_values = [{"committee_key": self.__committee_key, "key_fingerprint": fingerprint}]
        try:
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
        try:
            autogenerated_outcome = await self.autogenerate_keys_file()
        except Exception as e:
            return outcome.Error(e)
        return outcome.Result(
            types.LinkedCommittee(
                name=self.__committee_key,
                autogenerated_keys_file=autogenerated_outcome,
            )
        )

    async def autogenerate_keys_file(
        self,
    ) -> outcome.Outcome[str]:
        try:
            committee = await self.committee()
            if not committee.public_signing_keys:
                return outcome.Error(
                    storage.AccessError(
                        f"No keys found for committee {self.__committee_key} to generate KEYS file.", status=404
                    )
                )
            synced_path = await self._sync_committee_keys_file(self.__committee_key)
        except Exception as e:
            return outcome.Error(e)
        if synced_path is None:
            return outcome.Error(
                storage.AccessError(
                    f"No keys found for committee {self.__committee_key} to generate KEYS file.", status=404
                )
            )
        return outcome.Result(synced_path)

    async def committee(self) -> sql.Committee:
        return await self.__data.committee(key=self.__committee_key, _public_signing_keys=True).demand(
            storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
        )

    @property
    def committee_key(self) -> str:
        return self.__committee_key

    async def ensure_associated(self, keys_file_text: str) -> outcome.List[types.Key]:
        outcomes: outcome.List[types.Key] = await self.__ensure(keys_file_text, associate=True)
        if outcomes.any_result:
            await self.autogenerate_keys_file()
        return outcomes

    async def ensure_stored(self, keys_file_text: str) -> outcome.List[types.Key]:
        outcomes: outcome.List[types.Key] = await self.__ensure(keys_file_text, associate=False)
        if outcomes.any_result:
            await self.autogenerate_keys_file()
        return outcomes

    async def import_keys_file(
        self, project_key: safe.ProjectKey, version_key: safe.VersionKey
    ) -> outcome.List[types.Key]:
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

        outcomes = await self.ensure_associated(keys_file_text)
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
        return outcomes

    def __block_models(self, key_block: str, ldap_data: cache.EmailUidLookup) -> list[types.Key | Exception]:
        try:
            public_key, _ = openpgp.PublicKey.from_armor(key_block)
        except Exception as e:
            raise ValueError(f"Error loading OpenPGP key block: {e}") from e
        key_list = []
        try:
            key_model = self.public_key_model(public_key, ldap_data, original_key_block=key_block)
            _validate_key_strength(key_model.algorithm, key_model.length, key_model.created)
            key = types.Key(status=types.KeyStatus.PARSED, key_model=key_model)
            key_list.append(key)
        except Exception as e:
            key_list.append(e)
        return key_list

    async def __database_add_models(
        self, outcomes: outcome.List[types.Key], associate: bool = True
    ) -> outcome.List[types.Key]:
        # Try to upsert all models and link to the committee in one transaction
        try:
            outcomes = await self.__database_add_models_core(outcomes, associate=associate)
        except Exception as e:
            # This logging is just so that ruff does not erase e
            log.info(f"Post-parse error: {e}")

            def raise_post_parse_error(key: types.Key) -> NoReturn:
                nonlocal e
                # We assume here that the transaction was rolled back correctly
                key = types.Key(status=types.KeyStatus.PARSED, key_model=key.key_model)
                raise types.PublicKeyError(key, e)

            outcomes.update_roes(Exception, raise_post_parse_error)
        return outcomes

    async def __database_add_models_core(  # noqa: C901
        self,
        outcomes: outcome.List[types.Key],
        associate: bool = True,
    ) -> outcome.List[types.Key]:
        via = sql.validate_instrumented_attribute
        key_list = outcomes.results()

        await self.__data.begin_immediate()
        committee = await self.committee()

        key_values = [key.key_model.model_dump(exclude={"committees"}) for key in key_list]
        if key_values:
            stmt = sqlite.insert(sql.PublicSigningKey).values(key_values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["fingerprint"],
                set_={"apache_uid": stmt.excluded.apache_uid},
            )
            key_insert_result = await self.__data.execute(
                stmt.returning(via(sql.PublicSigningKey.fingerprint)),
            )
            key_inserts = {row.fingerprint for row in key_insert_result}
            log.info(f"Inserted or updated {util.plural(len(key_inserts), 'key')}")
        else:
            # TODO: Warn the user about any keys that were already inserted
            key_inserts = set()
            log.info("Inserted 0 keys (no keys to insert)")

        def replace_with_inserted(key: types.Key) -> types.Key:
            if key.key_model.fingerprint in key_inserts:
                key.status = types.KeyStatus.INSERTED
            return key

        outcomes.update_roes(Exception, replace_with_inserted)

        persisted_fingerprints = {v["fingerprint"] for v in key_values}
        await self.__data.flush()

        existing_fingerprints = {k.fingerprint for k in committee.public_signing_keys}
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

            def replace_with_linked(key: types.Key) -> types.Key:
                # nonlocal link_inserts
                match key:
                    case types.Key(status=types.KeyStatus.INSERTED):
                        if key.key_model.fingerprint in link_inserts:
                            key.status = types.KeyStatus.INSERTED_AND_LINKED
                    case types.Key(status=types.KeyStatus.PARSED):
                        if key.key_model.fingerprint in link_inserts:
                            key.status = types.KeyStatus.LINKED
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
        return outcomes

    async def __ensure(
        self,
        keys_file_text: str,
        associate: bool = True,
    ) -> outcome.List[types.Key]:
        outcomes = outcome.List[types.Key]()
        try:
            ldap_data = await cache.email_uid_view_or_live()
            key_blocks = util.parse_key_blocks(keys_file_text)
        except Exception as e:
            outcomes.append_error(e)
            return outcomes
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

    async def delete_committee_keys(self) -> tuple[int, int]:
        via = sql.validate_instrumented_attribute
        committee_query = self.__data.committee(key=self.__committee_key)
        committee_query.query = committee_query.query.options(
            orm.selectinload(via(sql.Committee.public_signing_keys)).selectinload(via(sql.PublicSigningKey.committees))
        )
        committee = await committee_query.demand(
            storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
        )

        keys_to_check = list(committee.public_signing_keys)
        if not keys_to_check:
            return (0, 0)

        num_unlinked = len(keys_to_check)
        fingerprints: list[basic.JSON] = [key_obj.fingerprint for key_obj in keys_to_check]
        committee.public_signing_keys.clear()
        await self.__data.flush()

        num_deleted = 0
        for key_obj in keys_to_check:
            if not key_obj.committees:
                await self.__data.delete(key_obj)
                num_deleted += 1

        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            keys_unlinked=num_unlinked,
            keys_deleted=num_deleted,
            fingerprints=fingerprints,
        )
        try:
            await self._sync_committee_keys_file(self.__committee_key)
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

        return (num_unlinked, num_deleted)


def _validate_key_strength(algorithm: int, length: int, created: datetime.datetime) -> None:
    """Raise ValueError if the key is recently-generated and does not meet the minimum cryptographic strength."""
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
