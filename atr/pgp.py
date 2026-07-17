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

import dataclasses
import datetime
from typing import Final

import openpgp

_SUBKEY_BINDING_SIGNATURE_TYPE: Final[str] = "subkey-binding"


@dataclasses.dataclass(frozen=True)
class SigningKeyStatus:
    # False when the signature names no key we hold, in which case nothing below has been evaluated
    identified: bool
    # The earliest expiration across the primary key and the issuing subkey, whichever binds first
    expires: datetime.datetime | None
    # False only when the issuing key declares capabilities which exclude signing
    can_sign: bool


def key_expires_at(key: openpgp.PublicKey) -> datetime.datetime | None:
    effective = _effective_self_signature(key)
    if effective is None:
        return None
    key_expiration_seconds = effective.key_expiration_seconds
    if key_expiration_seconds is None:
        return None
    return datetime.datetime.fromtimestamp(key.created_at + key_expiration_seconds, tz=datetime.UTC)


def latest_self_signature(key: openpgp.PublicKey) -> openpgp.SignatureInfo | None:
    signatures = _direct_self_signatures(key)
    signatures.extend(_binding_self_signatures(key))
    return _latest_signature(signatures)


def latest_self_signature_created_at(key: openpgp.PublicKey) -> datetime.datetime | None:
    signature = latest_self_signature(key)
    if signature is None or signature.creation_time is None:
        return None
    return datetime.datetime.fromtimestamp(signature.creation_time, tz=datetime.UTC)


def signing_key_status(
    key: openpgp.PublicKey,
    issuer_fingerprints: set[str],
    issuer_key_ids: set[str],
) -> SigningKeyStatus:
    """Validity of the key which issued a signature, be that the primary key or one of its subkeys."""
    binding = _issuing_subkey_binding(key, issuer_fingerprints, issuer_key_ids)
    if binding is not None:
        # A subkey is only usable while its own binding and the primary above it both hold
        binding_signature = _latest_binding_signature(binding)
        expirations = [
            expiration
            for expiration in (key_expires_at(key), _subkey_expires_at(binding, binding_signature))
            if expiration is not None
        ]
        return SigningKeyStatus(
            identified=True,
            expires=min(expirations) if expirations else None,
            can_sign=_declares_signing(binding_signature),
        )

    if _issuer_is_primary(key, issuer_fingerprints, issuer_key_ids):
        return SigningKeyStatus(
            identified=True,
            expires=key_expires_at(key),
            can_sign=_declares_signing(_effective_self_signature(key)),
        )

    # Naming no key we hold is not the same as naming the primary, and a signature we can't attribute
    # is one we can't judge, so report it as such rather than borrowing the primary's answer
    return SigningKeyStatus(identified=False, expires=None, can_sign=False)


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


def _declares_signing(signature: openpgp.SignatureInfo | None) -> bool:
    # Every usable key carries a self-signature, so its absence means a key we can't read rather than
    # one which permits everything
    if signature is None:
        return False
    flags = signature.key_flags
    # An absent key flags subpacket reads as every flag unset, which we can't tell apart from a key
    # declaring no capabilities at all. Older keys often declare nothing, so treat silence as
    # permission and only refuse a key which positively declares capabilities excluding signing
    declares_any = (
        flags.certify
        or flags.sign
        or flags.encrypt_communications
        or flags.encrypt_storage
        or flags.authenticate
        or flags.timestamping
    )
    if not declares_any:
        return True
    return flags.sign


def _direct_self_signatures(key: openpgp.PublicKey) -> list[openpgp.SignatureInfo]:
    self_fingerprint = key.fingerprint.lower()
    self_key_id = key.key_id.lower()
    return [
        signature
        for signature in key.direct_signature_infos()
        if _signature_is_self(signature, self_fingerprint, self_key_id)
    ]


def _effective_self_signature(key: openpgp.PublicKey) -> openpgp.SignatureInfo | None:
    direct_sigs = _direct_self_signatures(key)
    binding_sigs = _binding_self_signatures(key)
    if key.version >= 6:
        return _latest_signature(direct_sigs)
    if binding_sigs:
        return _latest_signature(binding_sigs)
    return _latest_signature(direct_sigs)


def _issuer_is_primary(
    key: openpgp.PublicKey,
    issuer_fingerprints: set[str],
    issuer_key_ids: set[str],
) -> bool:
    return (key.fingerprint.lower() in issuer_fingerprints) or (key.key_id.lower() in issuer_key_ids)


def _issuing_subkey_binding(
    key: openpgp.PublicKey,
    issuer_fingerprints: set[str],
    issuer_key_ids: set[str],
) -> openpgp.SubkeyBindingInfo | None:
    for binding in key.subkey_bindings():
        if binding.fingerprint.lower() in issuer_fingerprints:
            return binding
        if binding.key_id.lower() in issuer_key_ids:
            return binding
    return None


def _latest_binding_signature(binding: openpgp.SubkeyBindingInfo) -> openpgp.SignatureInfo | None:
    return _latest_signature(
        [signature for signature in binding.signatures if signature.signature_type == _SUBKEY_BINDING_SIGNATURE_TYPE]
    )


def _latest_signature(signatures: list[openpgp.SignatureInfo]) -> openpgp.SignatureInfo | None:
    if not signatures:
        return None
    return max(signatures, key=lambda signature: signature.creation_time or 0)


def _signature_is_self(signature: openpgp.SignatureInfo, self_fingerprint: str, self_key_id: str) -> bool:
    fingerprints = {fingerprint.lower() for fingerprint in signature.issuer_fingerprints}
    key_ids = {key_id.lower() for key_id in signature.issuer_key_ids}
    return (self_fingerprint in fingerprints) or (self_key_id in key_ids)


def _subkey_expires_at(
    binding: openpgp.SubkeyBindingInfo,
    signature: openpgp.SignatureInfo | None,
) -> datetime.datetime | None:
    if signature is None:
        return None
    key_expiration_seconds = signature.key_expiration_seconds
    if key_expiration_seconds is None:
        return None
    # Subkey expiration counts from the subkey's own creation, not the primary's
    return datetime.datetime.fromtimestamp(binding.created_at + key_expiration_seconds, tz=datetime.UTC)
