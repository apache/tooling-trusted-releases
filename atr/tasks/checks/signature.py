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
import datetime
import time
from typing import Any, Final

import openpgp
import sqlalchemy.exc
import sqlmodel

import atr.db as db
import atr.log as log
import atr.models.results as results
import atr.models.sql as sql
import atr.pgp as pgp
import atr.tasks.checks as checks
import atr.tasks.task as task
import atr.util as util

# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = []
INPUT_EXTRA_ARGS: Final[list[str]] = [
    "committee_key",
    "committee_signing_keys",
    "unsuffixed_file_hash",
    "unsuffixed_file_uploaders",
]
CHECK_VERSION: Final[str] = "12"


async def check(args: checks.FunctionArguments) -> results.Results | None:
    """Check a signature file."""
    recorder = await args.recorder(CHECK_VERSION)
    if not (primary_abs_path := await recorder.abs_path()):
        return None

    if not (primary_rel_path := args.primary_rel_path):
        raise ValueError("Primary relative path is required")

    artifact_rel_path = str(primary_rel_path).removesuffix(".asc")
    if not (artifact_abs_path := await recorder.abs_path(artifact_rel_path)):
        return None

    committee_key = args.extra_args.get("committee_key")
    if not isinstance(committee_key, str):
        raise ValueError("Committee name is required")

    log.info(
        f"Checking signature {primary_abs_path} for {artifact_abs_path}"
        f" using {committee_key} keys (rel: {primary_rel_path})"
    )

    uploader_uids = set(args.extra_args.get("unsuffixed_file_uploaders", []))
    try:
        result_data = await _check_core_logic(
            committee_key=committee_key,
            artifact_path=str(artifact_abs_path),
            signature_path=str(primary_abs_path),
            uploader_uids=uploader_uids,
        )
    except (OSError, sqlalchemy.exc.SQLAlchemyError) as e:
        raise task.CheckRetryableError("Error during signature check execution", {"error": str(e)}) from e

    match result_data:
        case {"error": error} if error:
            await recorder.blocker(error, result_data)
        case _ if result_data.get("verified"):
            await recorder.note("Signature verified successfully", result_data)
            if not result_data["signer_is_uploader"]:
                await _record_uploader_concern(args, recorder, uploader_uids, result_data)
        case _:
            raise RuntimeError("Signature verification failed for unknown reasons")

    return None


async def _check_core_logic(
    committee_key: str, artifact_path: str, signature_path: str, uploader_uids: set[str]
) -> dict[str, Any]:
    """Verify a signature file using the committee's public signing keys."""
    log.info(f"Attempting to fetch keys for committee_key: '{committee_key}'")
    async with db.session() as session:
        statement = (
            sqlmodel.select(sql.SigningCertificate)
            .join(sql.KeyLink)
            .join(sql.Committee)
            .where(
                sql.validate_instrumented_attribute(sql.Committee.key) == committee_key,
                sql.validate_instrumented_attribute(sql.SigningCertificate.deleted).is_(None),
            )
        )
        result = await session.execute(statement)
        db_public_keys = result.scalars().all()
    log.info(f"Found {len(db_public_keys)} public keys for committee_key: '{committee_key}'")
    apache_uid_map = {}
    uploader_fingerprints = set()
    for key in db_public_keys:
        if not key.fingerprint:
            continue
        fingerprint = key.fingerprint.lower()
        automated = util.is_automated_release_signing_uid(key.primary_declared_uid, committee_key)
        apache_uid_map[fingerprint] = bool(key.apache_uid) or automated
        if automated or (key.apache_uid in uploader_uids):
            uploader_fingerprints.add(fingerprint)

    committee_keys = []
    for key in db_public_keys:
        armored = key.ascii_armored_key
        if isinstance(armored, bytes):
            armored = armored.decode("utf-8", errors="replace")
        committee_keys.append((key.fingerprint.lower(), armored))

    return await asyncio.to_thread(
        _check_core_logic_verify_signature,
        signature_path=signature_path,
        artifact_path=artifact_path,
        committee_keys=committee_keys,
        apache_uid_map=apache_uid_map,
        uploader_fingerprints=uploader_fingerprints,
    )


def _check_core_logic_verify_signature(
    signature_path: str,
    artifact_path: str,
    committee_keys: list[tuple[str, str]],
    apache_uid_map: dict[str, bool],
    uploader_fingerprints: set[str],
) -> dict[str, Any]:
    """Verify an OpenPGP signature for a file."""
    start = time.perf_counter_ns()
    public_keys = _parse_public_keys(committee_keys)
    if not public_keys:
        log.warning("No fingerprints found after parsing keys")
        return {
            "verified": False,
            "error": "No public keys found to verify signature",
            "error_kind": "missing_keys",
            "hint": "If your signing key is expected then you can rectify this by",
            "hint_link_text": "associating your key",
            "hint_link": "/keys",
            "debug_info": _debug_info(
                key=None,
                signature_info=None,
                status="No public keys found to verify signature",
                valid=False,
                num_committee_keys=len(committee_keys),
                key_has_apache_uid=False,
            ),
        }
    end = time.perf_counter_ns()
    log.info(f"Parsing of {util.plural(len(committee_keys), 'key')} took {(end - start) / 1000000} ms")

    try:
        with open(signature_path, "rb") as sig_file:
            armored = sig_file.read()
    except OSError as e:
        raise task.CheckRetryableError("Unable to read the signature file", {"error": str(e)}) from e
    try:
        signature, _ = openpgp.composed.DetachedSignature.from_armor(armored.decode("utf-8"))
    except Exception as e:
        return {
            "verified": False,
            "error": "No valid signature found",
            "error_kind": "missing_signature",
            "debug_info": _debug_info(
                key=None,
                signature_info=None,
                status=str(e),
                valid=False,
                num_committee_keys=len(committee_keys),
                key_has_apache_uid=False,
            ),
        }
    signature_info = signature.signature
    try:
        with open(artifact_path, "rb"):
            pass
    except FileNotFoundError:
        return {
            "verified": False,
            "error": "Referenced artifact not found",
            "error_kind": "missing_artifact",
            "debug_info": _debug_info(
                key=None,
                signature_info=signature_info,
                status="Referenced artifact not found",
                valid=False,
                num_committee_keys=len(committee_keys),
                key_has_apache_uid=False,
            ),
        }
    except OSError as e:
        raise task.CheckRetryableError("Unable to read the artifact file", {"error": str(e)}) from e
    issuer_fingerprints = {fingerprint.lower() for fingerprint in signature_info.issuer_fingerprint()}
    issuer_key_ids = {key_id.lower() for key_id in signature_info.issuer_key_id()}
    candidate_keys = [key for key in public_keys if _key_matches_signature(key, issuer_fingerprints, issuer_key_ids)]

    matched_key = _first_verifying_key(signature, candidate_keys, artifact_path)
    if matched_key is None:
        return {
            "verified": False,
            "error": "No valid signature found",
            "error_kind": "missing_signature",
            "debug_info": _debug_info(
                key=None,
                signature_info=signature_info,
                status="No valid signature found",
                valid=False,
                num_committee_keys=len(committee_keys),
                key_has_apache_uid=False,
            ),
        }

    apache_uid_ok = apache_uid_map.get(matched_key.fingerprint.lower(), False)

    status = pgp.signing_key_status(matched_key, issuer_fingerprints, issuer_key_ids)
    refusal = _key_refusal_result(
        status=status,
        matched_key=matched_key,
        signature_info=signature_info,
        num_committee_keys=len(committee_keys),
        key_has_apache_uid=apache_uid_ok,
    )
    if refusal is not None:
        return refusal

    debug_info = _debug_info(
        key=matched_key,
        signature_info=signature_info,
        status="Valid signature",
        valid=True,
        num_committee_keys=len(committee_keys),
        key_has_apache_uid=apache_uid_ok,
        signing_key_fingerprint=status.fingerprint,
    )

    if not apache_uid_ok:
        debug_info["status"] = "Invalid: Key lacks ASF UID"
        return {
            "verified": False,
            "error": "Verifying key lacks an ASF UID",
            "error_kind": "no_asf_uid",
            "debug_info": debug_info,
        }

    key_id = signature_info.issuer_key_id()[0] if signature_info.issuer_key_id() else matched_key.key_id
    username = _signer_username(matched_key, signature_info) or "Unknown"
    timestamp = signature_info.created()
    return {
        "verified": True,
        "key_id": key_id,
        "timestamp": timestamp,
        "username": username,
        # The key which actually signed, which is a subkey wherever one was used. Artifact
        # provenance is recorded from this, so it must not be widened to the certificate
        "fingerprint": status.fingerprint,
        "certificate_fingerprint": matched_key.fingerprint.lower(),
        "signer_is_uploader": matched_key.fingerprint.lower() in uploader_fingerprints,
        "status": "Valid signature",
        "debug_info": debug_info,
    }


def _debug_info(
    *,
    key: openpgp.composed.SignedPublicKey | None,
    signature_info: openpgp.packet.Signature | None,
    status: str,
    valid: bool,
    num_committee_keys: int,
    key_has_apache_uid: bool,
    signing_key_fingerprint: str | None = None,
) -> dict[str, Any]:
    certificate_fingerprint = key.fingerprint.lower() if (key is not None) else "Not available"
    # key_id below names the issuing key, so the fingerprint beside it has to name the same one
    fingerprint = signing_key_fingerprint or certificate_fingerprint
    key_id = key.key_id if (key is not None) else "Not available"
    creation_time = signature_info.created() if (signature_info is not None) else None
    username = _signer_username(key, signature_info) if ((key is not None) and (signature_info is not None)) else None
    return {
        "key_id": (
            signature_info.issuer_key_id()[0] if (signature_info and signature_info.issuer_key_id()) else key_id
        ),
        "fingerprint": fingerprint,
        "pubkey_fingerprint": certificate_fingerprint,
        "creation_date": creation_time if (creation_time is not None) else "Not available",
        "timestamp": creation_time if (creation_time is not None) else "Not available",
        "username": username or "Not available",
        "status": status,
        "valid": valid,
        "trust_level": "Not available",
        "trust_text": "Not available",
        "stderr": "Not available",
        "num_committee_keys": num_committee_keys,
        "key_has_apache_uid": key_has_apache_uid,
        "hash_algorithm": signature_info.hash_alg() if (signature_info is not None) else "Not available",
        "signature_type": signature_info.typ() if (signature_info is not None) else "Not available",
        # The signature no longer carries its own algorithm, so this reports the certificate's instead
        "public_key_algorithm": (key.public_key_algorithm if (key is not None) else "Not available"),
    }


def _expired_key_result(
    *,
    matched_key: openpgp.composed.SignedPublicKey,
    signature_info: openpgp.packet.Signature,
    expires: datetime.datetime,
    num_committee_keys: int,
    key_has_apache_uid: bool,
) -> dict[str, Any]:
    debug_info = _debug_info(
        key=matched_key,
        signature_info=signature_info,
        status="Invalid: Key expired",
        valid=False,
        num_committee_keys=num_committee_keys,
        key_has_apache_uid=key_has_apache_uid,
    )
    return {
        "verified": False,
        "error": "Signing key has expired",
        "error_kind": "key_expired",
        "hint": f"The signing key expired on {expires.date().isoformat()}. Renew it, re-sign, and",
        "hint_link_text": "update your key",
        "hint_link": "/keys",
        "debug_info": debug_info,
    }


def _first_verifying_key(
    signature: openpgp.composed.DetachedSignature,
    candidate_keys: list[openpgp.composed.SignedPublicKey],
    artifact_path: str,
) -> openpgp.composed.SignedPublicKey | None:
    for candidate_key in candidate_keys:
        try:
            signature.verify_file(candidate_key, artifact_path)
            return candidate_key
        except Exception as e:
            log.debug(f"Signature verification failed for key {candidate_key.fingerprint}: {e}")
            continue
    return None


def _key_matches_signature(
    key: openpgp.composed.SignedPublicKey,
    issuer_fingerprints: set[str],
    issuer_key_ids: set[str],
) -> bool:
    if (not issuer_fingerprints) and (not issuer_key_ids):
        return True

    key_fingerprints = {key.fingerprint.lower()}
    key_fingerprints.update(subkey.key.fingerprint.lower() for subkey in key.public_subkeys)
    if key_fingerprints.intersection(issuer_fingerprints):
        return True

    key_ids = {key.key_id.lower()}
    key_ids.update(subkey.key.key_id.lower() for subkey in key.public_subkeys)
    return bool(key_ids.intersection(issuer_key_ids))


def _key_refusal_result(
    *,
    status: pgp.SigningKeyStatus,
    matched_key: openpgp.composed.SignedPublicKey,
    signature_info: openpgp.packet.Signature,
    num_committee_keys: int,
    key_has_apache_uid: bool,
) -> dict[str, Any] | None:
    """The blocker for a key we won't accept as the signer, or None where the key is acceptable."""
    if not status.identified:
        return _unidentified_key_result(
            matched_key=matched_key,
            signature_info=signature_info,
            num_committee_keys=num_committee_keys,
            key_has_apache_uid=key_has_apache_uid,
        )
    if status.revoked:
        return _revoked_key_result(
            matched_key=matched_key,
            signature_info=signature_info,
            num_committee_keys=num_committee_keys,
            key_has_apache_uid=key_has_apache_uid,
        )
    if (status.expires is not None) and (status.expires <= datetime.datetime.now(datetime.UTC)):
        return _expired_key_result(
            matched_key=matched_key,
            signature_info=signature_info,
            expires=status.expires,
            num_committee_keys=num_committee_keys,
            key_has_apache_uid=key_has_apache_uid,
        )
    if not status.can_sign:
        return _not_signing_key_result(
            matched_key=matched_key,
            signature_info=signature_info,
            num_committee_keys=num_committee_keys,
            key_has_apache_uid=key_has_apache_uid,
        )
    return None


def _not_signing_key_result(
    *,
    matched_key: openpgp.composed.SignedPublicKey,
    signature_info: openpgp.packet.Signature,
    num_committee_keys: int,
    key_has_apache_uid: bool,
) -> dict[str, Any]:
    debug_info = _debug_info(
        key=matched_key,
        signature_info=signature_info,
        status="Invalid: Key not permitted to sign",
        valid=False,
        num_committee_keys=num_committee_keys,
        key_has_apache_uid=key_has_apache_uid,
    )
    return {
        "verified": False,
        "error": "Signature was made by a key which is not permitted to sign",
        "error_kind": "not_signing_key",
        "hint": "Sign the artifact with a signing key, then",
        "hint_link_text": "check your keys",
        "hint_link": "/keys",
        "debug_info": debug_info,
    }


def _parse_public_keys(committee_keys: list[tuple[str, str]]) -> list[openpgp.composed.SignedPublicKey]:
    public_keys: list[openpgp.composed.SignedPublicKey] = []
    for fingerprint, ascii_armored_key in committee_keys:
        try:
            public_key = pgp.certificate_for_fingerprint(ascii_armored_key, fingerprint)
        except Exception as e:
            log.warning(f"Failed to parse committee public key {fingerprint}: {e}")
            continue
        if public_key is None:
            log.warning(f"Stored block for {fingerprint} holds another key, skipping")
            continue
        public_keys.append(public_key)
    return public_keys


async def _record_uploader_concern(
    args: checks.FunctionArguments,
    recorder: checks.Recorder,
    uploader_uids: set[str],
    result_data: dict[str, Any],
) -> None:
    uploader_recorder = await checks.Recorder.create(
        checker=checks.function_key(check) + "_uploader_mismatch",
        checker_version=CHECK_VERSION,
        inputs_hash=recorder.input_hash or "",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        primary_rel_path=args.primary_rel_path,
        afresh=False,
        embargoed=recorder.embargoed,
    )
    await uploader_recorder.concern(
        "The artifact was not uploaded by the owner of the signing key",
        {**result_data, "uploader_uids": sorted(uploader_uids)},
    )


def _revoked_key_result(
    *,
    matched_key: openpgp.composed.SignedPublicKey,
    signature_info: openpgp.packet.Signature,
    num_committee_keys: int,
    key_has_apache_uid: bool,
) -> dict[str, Any]:
    debug_info = _debug_info(
        key=matched_key,
        signature_info=signature_info,
        status="Invalid: Signing key revoked",
        valid=False,
        num_committee_keys=num_committee_keys,
        key_has_apache_uid=key_has_apache_uid,
    )
    return {
        "verified": False,
        "error": "Signature was made by a revoked key",
        "error_kind": "revoked_key",
        "hint": "Sign the artifact with a key which has not been revoked, then",
        "hint_link_text": "check your keys",
        "hint_link": "/keys",
        "debug_info": debug_info,
    }


def _signer_username(key: openpgp.composed.SignedPublicKey, signature_info: openpgp.packet.Signature) -> str | None:
    signer_user_id = signature_info.signers_userid()
    if signer_user_id:
        return signer_user_id
    for user in key.details.users:
        if user.is_primary:
            return user.user_id
    if key.user_ids:
        return key.user_ids[0]
    return None


def _unidentified_key_result(
    *,
    matched_key: openpgp.composed.SignedPublicKey,
    signature_info: openpgp.packet.Signature,
    num_committee_keys: int,
    key_has_apache_uid: bool,
) -> dict[str, Any]:
    debug_info = _debug_info(
        key=matched_key,
        signature_info=signature_info,
        status="Invalid: Signing key not identified",
        valid=False,
        num_committee_keys=num_committee_keys,
        key_has_apache_uid=key_has_apache_uid,
    )
    return {
        "verified": False,
        "error": "Signature does not identify the key which made it",
        "error_kind": "unidentified_key",
        "hint": "Sign the artifact with a key which records its own identity in the signature, then",
        "hint_link_text": "check your keys",
        "hint_link": "/keys",
        "debug_info": debug_info,
    }
