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
from typing import Final, Literal

import openpgp

_CERTIFICATION_REVOCATION_SIGNATURE_TYPE: Final[str] = "cert-revocation"
# The self-certifications which carry key expiry and capabilities. A revocation sits alongside these
# on a user binding, but declares neither, so it can't stand in for one
_CERTIFICATION_SIGNATURE_TYPES: Final[frozenset[str]] = frozenset(
    {"cert-generic", "cert-persona", "cert-casual", "cert-positive"}
)
_KEY_REVOCATION_SIGNATURE_TYPE: Final[str] = "key-revocation"
_SUBKEY_BINDING_SIGNATURE_TYPE: Final[str] = "subkey-binding"
_SUBKEY_REVOCATION_SIGNATURE_TYPE: Final[str] = "subkey-revocation"

ComponentKind = Literal["primary", "user-id", "user-attribute", "subkey"]


@dataclasses.dataclass(frozen=True)
class Component:
    kind: ComponentKind
    label: str
    flags: str | None
    revoked: bool
    self_signatures: int
    other_signatures: int
    facts: "SigningKeyFacts | None"


@dataclasses.dataclass(frozen=True)
class SigningKeyFacts:
    """The stored form of one key which can carry a signature, the primary or one of its subkeys."""

    fingerprint: str
    key_id: str
    is_primary: bool
    algorithm: str
    length_bits: int | None
    created: datetime.datetime
    expires: datetime.datetime | None
    revoked: bool
    can_sign: bool


@dataclasses.dataclass(frozen=True)
class SigningKeyStatus:
    # False when the signature names no key we hold, in which case nothing below has been evaluated
    identified: bool
    # The key which issued the signature, being a subkey wherever one was used, else the primary
    fingerprint: str | None
    # The earliest expiration across the primary key and the issuing subkey, whichever binds first
    expires: datetime.datetime | None
    # False only when the issuing key declares capabilities which exclude signing
    can_sign: bool
    # True where the issuing key carries a revocation, or hangs beneath a primary which does
    revoked: bool


def certificate_block_shape(keys: list[openpgp.composed.SignedPublicKey], fingerprint: str) -> str:
    fingerprints = [key.fingerprint.lower() for key in keys]
    matches = fingerprints.count(fingerprint.lower())
    if not keys:
        return "empty"
    if matches == 0:
        return "wrong-fingerprint" if (len(keys) == 1) else "no-own-certificate"
    if matches > 1:
        return "own-certificate-repeated"
    if len(keys) == 1:
        return "single"
    return "multi-own-first" if (fingerprints[0] == fingerprint.lower()) else "multi-own-not-first"


def certificate_components(
    key: openpgp.composed.SignedPublicKey, *, at: datetime.datetime | None = None
) -> list[Component]:
    at = at or datetime.datetime.now(datetime.UTC)
    timestamp = at.timestamp()
    fingerprint = key.fingerprint.lower()
    key_id = key.key_id.lower()
    facts = signing_key_facts(key, at=at)
    primary_signatures = [*key.details.direct_signatures, *key.details.revocation_signatures]
    components = [
        _key_component(
            "primary", facts[0], _effective_self_signature(key, timestamp), primary_signatures, fingerprint, key_id
        )
    ]
    components.extend(
        _identity_component("user-id", user.id, user.signatures, fingerprint, key_id, timestamp)
        for user in key.details.users
    )
    for attribute in key.details.user_attributes:
        label = _attribute_label(attribute.attr)
        components.append(
            _identity_component("user-attribute", label, attribute.signatures, fingerprint, key_id, timestamp)
        )
    for subkey_facts, subkey in zip(facts[1:], key.public_subkeys, strict=True):
        binding = _latest_binding_signature(subkey, timestamp)
        components.append(_key_component("subkey", subkey_facts, binding, subkey.signatures, fingerprint, key_id))
    return components


def certificate_for_fingerprint(armored: str, fingerprint: str) -> openpgp.composed.SignedPublicKey | None:
    keys, _ = openpgp.composed.SignedPublicKey.from_armor_many(armored)
    matches = [key for key in keys if key.fingerprint.lower() == fingerprint.lower()]
    if len(matches) > 1:
        raise ValueError(f"Certificate {fingerprint} appears {len(matches)} times in the block")
    return matches[0] if matches else None


def key_expires_at(
    key: openpgp.composed.SignedPublicKey, *, at: datetime.datetime | None = None
) -> datetime.datetime | None:
    effective = _effective_self_signature(key, (at or datetime.datetime.now(datetime.UTC)).timestamp())
    if effective is None:
        return None
    key_expiration_seconds = effective.key_expiration_time()
    if not key_expiration_seconds:
        return None
    return datetime.datetime.fromtimestamp(key.created_at + key_expiration_seconds, tz=datetime.UTC)


def latest_self_signature(
    key: openpgp.composed.SignedPublicKey, *, at: datetime.datetime | None = None
) -> openpgp.packet.Signature | None:
    timestamp = (at or datetime.datetime.now(datetime.UTC)).timestamp()
    signatures = _direct_self_signatures(key)
    signatures.extend(_binding_self_signatures(key, timestamp))
    return _latest_signature(signatures, timestamp)


def latest_self_signature_created_at(
    key: openpgp.composed.SignedPublicKey, *, at: datetime.datetime | None = None
) -> datetime.datetime | None:
    signature = latest_self_signature(key, at=at)
    if signature is None:
        return None
    created = signature.created()
    if created is None:
        return None
    return datetime.datetime.fromtimestamp(created, tz=datetime.UTC)


def public_params_bits(public_params: openpgp.types.PublicParams) -> int | None:
    for bits in (public_params.rsa_bits, public_params.dsa_bits, public_params.curve_bits):
        if isinstance(bits, int):
            return bits
    return None


def revocations_dropped(
    previous: openpgp.composed.SignedPublicKey,
    incoming: openpgp.composed.SignedPublicKey,
    *,
    at: datetime.datetime | None = None,
) -> set[str]:
    """Signing keys revoked in `previous` but still present and no longer revoked in `incoming`."""
    # A revocation is irreversible and survives a minimised re-export, so a signing key coming back from
    # revoked to live is a stripped block. A key dropped altogether is left to the caller
    at = at or datetime.datetime.now(datetime.UTC)
    incoming_facts = {facts.fingerprint: facts for facts in signing_key_facts(incoming, at=at)}
    dropped = set()
    for facts in signing_key_facts(previous, at=at):
        if not facts.revoked:
            continue
        current = incoming_facts.get(facts.fingerprint)
        if (current is not None) and (not current.revoked):
            dropped.add(facts.fingerprint)
    return dropped


def signing_key_facts(
    key: openpgp.composed.SignedPublicKey, *, at: datetime.datetime | None = None
) -> list[SigningKeyFacts]:
    """Every key in a certificate which can carry a signature, the primary first."""
    at = at or datetime.datetime.now(datetime.UTC)
    timestamp = at.timestamp()
    primary = key.primary_key
    primary_revoked = _primary_is_revoked(key)
    primary_expires = key_expires_at(key, at=at)
    facts = [
        SigningKeyFacts(
            fingerprint=key.fingerprint.lower(),
            key_id=key.key_id.lower(),
            is_primary=True,
            algorithm=key.public_key_algorithm,
            length_bits=public_params_bits(key.public_params),
            created=datetime.datetime.fromtimestamp(key.created_at, tz=datetime.UTC),
            expires=primary_expires,
            revoked=primary_revoked,
            can_sign=_declares_signing(_effective_self_signature(key, timestamp)),
        )
    ]

    for subkey in key.public_subkeys:
        binding_signature = _latest_binding_signature(subkey, timestamp)
        # A subkey is only usable while its own binding and the primary above it both hold
        expirations = [
            expiration
            for expiration in (primary_expires, _subkey_expires_at(subkey, binding_signature))
            if expiration is not None
        ]
        facts.append(
            SigningKeyFacts(
                fingerprint=subkey.key.fingerprint.lower(),
                key_id=subkey.key.key_id.lower(),
                is_primary=False,
                algorithm=subkey.key.public_key_algorithm,
                length_bits=public_params_bits(subkey.key.public_params),
                created=datetime.datetime.fromtimestamp(subkey.key.created_at, tz=datetime.UTC),
                expires=min(expirations) if expirations else None,
                revoked=primary_revoked or _subkey_is_revoked(primary, subkey),
                can_sign=_declares_signing(binding_signature),
            )
        )
    return facts


def signing_key_status(
    key: openpgp.composed.SignedPublicKey,
    issuer_fingerprints: set[str],
    issuer_key_ids: set[str],
    *,
    at: datetime.datetime | None = None,
) -> SigningKeyStatus:
    """Validity of the key which issued a signature, be that the primary key or one of its subkeys."""
    at = at or datetime.datetime.now(datetime.UTC)
    timestamp = at.timestamp()
    primary = key.primary_key
    primary_revoked = _primary_is_revoked(key)
    subkey = _issuing_subkey(key, issuer_fingerprints, issuer_key_ids)
    if subkey is not None:
        # A subkey is only usable while its own binding and the primary above it both hold
        binding_signature = _latest_binding_signature(subkey, timestamp)
        expirations = [
            expiration
            for expiration in (key_expires_at(key, at=at), _subkey_expires_at(subkey, binding_signature))
            if expiration is not None
        ]
        return SigningKeyStatus(
            identified=True,
            fingerprint=subkey.key.fingerprint.lower(),
            expires=min(expirations) if expirations else None,
            can_sign=_declares_signing(binding_signature),
            # Revoking the primary revokes everything beneath it, so a live subkey binding isn't enough
            revoked=primary_revoked or _subkey_is_revoked(primary, subkey),
        )

    if _issuer_is_primary(key, issuer_fingerprints, issuer_key_ids):
        return SigningKeyStatus(
            identified=True,
            fingerprint=key.fingerprint.lower(),
            expires=key_expires_at(key, at=at),
            can_sign=_declares_signing(_effective_self_signature(key, timestamp)),
            revoked=primary_revoked,
        )

    # Naming no key we hold is not the same as naming the primary, and a signature we can't attribute
    # is one we can't judge, so report it as such rather than borrowing the primary's answer
    return SigningKeyStatus(identified=False, fingerprint=None, expires=None, can_sign=False, revoked=False)


def _attribute_label(attribute: openpgp.packet.UserAttribute) -> str:
    kind = " ".join(part for part in (attribute.kind, attribute.image_format) if part)
    return f"{kind} ({len(attribute.data)} bytes)"


def _binding_revoked(
    signatures: list[openpgp.packet.Signature], fingerprint: str, key_id: str, timestamp: float
) -> bool:
    candidates = [
        signature
        for signature in signatures
        if _signature_is_self(signature, fingerprint, key_id)
        and (
            (signature.typ() in _CERTIFICATION_SIGNATURE_TYPES)
            or (signature.typ() == _CERTIFICATION_REVOCATION_SIGNATURE_TYPE)
        )
    ]
    latest = _latest_signature(candidates, timestamp)
    return (latest is not None) and (latest.typ() == _CERTIFICATION_REVOCATION_SIGNATURE_TYPE)


def _binding_self_signatures(key: openpgp.composed.SignedPublicKey, timestamp: float) -> list[openpgp.packet.Signature]:
    self_fingerprint = key.fingerprint.lower()
    self_key_id = key.key_id.lower()
    active_users = [
        user
        for user in key.details.users
        if not _binding_revoked(user.signatures, self_fingerprint, self_key_id, timestamp)
    ]
    primary_users = [user for user in active_users if user.is_primary]
    chosen_users = primary_users or active_users
    binding_sigs: list[openpgp.packet.Signature] = []
    for user in chosen_users:
        binding_sigs.extend(
            signature
            for signature in user.signatures
            if _signature_is_self(signature, self_fingerprint, self_key_id)
            # A cert-revocation is self-issued too, but carries no expiry or key flags, so admitting it
            # here lets a revoked user id erase both for the whole key
            and (signature.typ() in _CERTIFICATION_SIGNATURE_TYPES)
        )
    return binding_sigs


def _declares_signing(signature: openpgp.packet.Signature | None) -> bool:
    # Every usable key carries a self-signature, so its absence means a key we can't read rather than
    # one which permits everything
    if signature is None:
        return False
    flags = signature.key_flags()
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


def _direct_self_signatures(key: openpgp.composed.SignedPublicKey) -> list[openpgp.packet.Signature]:
    self_fingerprint = key.fingerprint.lower()
    self_key_id = key.key_id.lower()
    return [
        signature
        for signature in key.details.direct_signatures
        if _signature_is_self(signature, self_fingerprint, self_key_id)
    ]


def _effective_self_signature(
    key: openpgp.composed.SignedPublicKey, timestamp: float
) -> openpgp.packet.Signature | None:
    direct = _latest_signature(_direct_self_signatures(key), timestamp)
    if key.version >= 6:
        return direct
    binding = _latest_signature(_binding_self_signatures(key, timestamp), timestamp)
    return binding if (binding is not None) else direct


def _identity_component(
    kind: ComponentKind,
    label: str,
    signatures: list[openpgp.packet.Signature],
    fingerprint: str,
    key_id: str,
    timestamp: float,
) -> Component:
    own, other = _signature_counts(signatures, fingerprint, key_id)
    return Component(kind, label, None, _binding_revoked(signatures, fingerprint, key_id, timestamp), own, other, None)


def _issuer_is_primary(
    key: openpgp.composed.SignedPublicKey,
    issuer_fingerprints: set[str],
    issuer_key_ids: set[str],
) -> bool:
    return (key.fingerprint.lower() in issuer_fingerprints) or (key.key_id.lower() in issuer_key_ids)


def _issuing_subkey(
    key: openpgp.composed.SignedPublicKey,
    issuer_fingerprints: set[str],
    issuer_key_ids: set[str],
) -> openpgp.composed.SignedPublicSubKey | None:
    for subkey in key.public_subkeys:
        if subkey.key.fingerprint.lower() in issuer_fingerprints:
            return subkey
        if subkey.key.key_id.lower() in issuer_key_ids:
            return subkey
    return None


def _key_component(
    kind: ComponentKind,
    facts: SigningKeyFacts,
    signature: openpgp.packet.Signature | None,
    signatures: list[openpgp.packet.Signature],
    fingerprint: str,
    key_id: str,
) -> Component:
    own, other = _signature_counts(signatures, fingerprint, key_id)
    return Component(kind, facts.fingerprint, _key_flags(signature), facts.revoked, own, other, facts)


def _key_flags(signature: openpgp.packet.Signature | None) -> str | None:
    if signature is None:
        return None
    flags = signature.key_flags()
    letters = (
        ("C", flags.certify),
        ("S", flags.sign),
        ("E", flags.encrypt_communications or flags.encrypt_storage),
        ("A", flags.authenticate),
    )
    return "".join(letter for letter, declared in letters if declared)


def _latest_binding_signature(
    subkey: openpgp.composed.SignedPublicSubKey, timestamp: float
) -> openpgp.packet.Signature | None:
    return _latest_signature(
        [signature for signature in subkey.signatures if signature.typ() == _SUBKEY_BINDING_SIGNATURE_TYPE], timestamp
    )


def _latest_signature(signatures: list[openpgp.packet.Signature], timestamp: float) -> openpgp.packet.Signature | None:
    valid = [signature for signature in signatures if _signature_active(signature, timestamp)]
    if not valid:
        return None
    return max(valid, key=lambda signature: signature.created() or 0)


def _primary_is_revoked(key: openpgp.composed.SignedPublicKey) -> bool:
    primary = key.primary_key
    for signature in key.details.revocation_signatures:
        if signature.typ() != _KEY_REVOCATION_SIGNATURE_TYPE:
            continue
        if _revocation_verifies(signature, primary):
            return True
    return False


def _revocation_verifies(signature: openpgp.packet.Signature, primary: openpgp.packet.PublicKey) -> bool:
    # A revocation only counts once we've checked the primary key actually made it. An unsigned or
    # forged revocation packet must not block a key, or anyone could deny a project its releases just
    # by dropping one into a KEYS file. A designated-revoker revocation is signed by someone other than
    # the primary, so it won't verify here and reads as not revoked, which is conservative and rare
    # enough to leave for now
    try:
        signature.verify_key(primary)
    except openpgp.errors.Error:
        return False
    return True


def _signature_active(signature: openpgp.packet.Signature, timestamp: float) -> bool:
    created = signature.created() or 0
    if created > timestamp:
        return False
    expiration = signature.signature_expiration_time()
    if not expiration:
        return True
    return (created + expiration) > timestamp


def _signature_counts(signatures: list[openpgp.packet.Signature], fingerprint: str, key_id: str) -> tuple[int, int]:
    own = sum(1 for signature in signatures if _signature_is_self(signature, fingerprint, key_id))
    return own, len(signatures) - own


def _signature_is_self(signature: openpgp.packet.Signature, self_fingerprint: str, self_key_id: str) -> bool:
    fingerprints = {fingerprint.lower() for fingerprint in signature.issuer_fingerprint()}
    key_ids = {key_id.lower() for key_id in signature.issuer_key_id()}
    return (self_fingerprint in fingerprints) or (self_key_id in key_ids)


def _subkey_expires_at(
    subkey: openpgp.composed.SignedPublicSubKey,
    signature: openpgp.packet.Signature | None,
) -> datetime.datetime | None:
    if signature is None:
        return None
    key_expiration_seconds = signature.key_expiration_time()
    if not key_expiration_seconds:
        return None
    # Subkey expiration counts from the subkey's own creation, not the primary's
    return datetime.datetime.fromtimestamp(subkey.key.created_at + key_expiration_seconds, tz=datetime.UTC)


def _subkey_is_revoked(primary: openpgp.packet.PublicKey, subkey: openpgp.composed.SignedPublicSubKey) -> bool:
    # As with the primary, only a revocation the primary actually made counts, so a forged
    # subkey-revocation can't be used to block a key. The revocation can share a timestamp with the
    # binding it kills, so a verifying one is enough on its own without weighing the two dates
    for signature in subkey.signatures:
        if signature.typ() != _SUBKEY_REVOCATION_SIGNATURE_TYPE:
            continue
        try:
            signature.verify_subkey_binding(primary, subkey.key)
        except openpgp.errors.Error:
            continue
        return True
    return False
