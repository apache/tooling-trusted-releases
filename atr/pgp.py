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

import base64
import dataclasses
import datetime
import itertools
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

_ARMOR_BEGIN: Final[str] = "-----BEGIN PGP PUBLIC KEY BLOCK-----"
_ARMOR_END: Final[str] = "-----END PGP PUBLIC KEY BLOCK-----"
_PRIMARY_KEY_TAG: Final[int] = 6
_PUBLIC_SUBKEY_TAG: Final[int] = 14
_SIGNATURE_TAG: Final[int] = 2
_SKIPPABLE_PACKET_TAGS: Final[frozenset[int]] = frozenset({10, 12, 21})
_USER_ATTRIBUTE_TAG: Final[int] = 17
_USER_ID_TAG: Final[int] = 13

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


def certificate_block_fingerprint(block: str) -> str:
    frames = _frames(_dearmored(block))
    if (not frames) or (frames[0][0] != _PRIMARY_KEY_TAG):
        raise ValueError("The block does not begin with a primary key packet")
    value = openpgp.packet.Packet.from_bytes(_framed(*frames[0])).value
    if not isinstance(value, openpgp.packet.PublicKey):
        raise ValueError("The primary packet is not a public key")
    return value.fingerprint.lower()


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


def certificate_blocks(text: str) -> list[str]:
    frames = _frames(_dearmored(text))
    starts = [index for index, frame in enumerate(frames) if frame[0] == _PRIMARY_KEY_TAG]
    bounds = [*starts, len(frames)]
    segments = [frames[begin:end] for begin, end in itertools.pairwise(bounds)]
    return [_armored(b"".join(_framed(tag, body) for tag, body in segment)) for segment in segments]


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


def merge_certificate_blocks(blocks: list[str]) -> str:
    if not blocks:
        raise ValueError("No certificate blocks to merge")
    grouped = [_certificate_groups(_frames(_dearmored(block))) for block in blocks]
    primary_bodies = {groups[0][0][1] for groups in grouped}
    if len(primary_bodies) > 1:
        raise ValueError("The blocks hold different primary keys")
    primary = openpgp.packet.Packet.from_bytes(_framed(*grouped[0][0][0])).value
    if not isinstance(primary, openpgp.packet.PublicKey):
        raise ValueError("The primary packet is not a public key")
    sections: dict[tuple[int, bytes], dict[tuple[int, bytes], None]] = {}
    for groups in grouped:
        for head, attached in groups:
            merged = sections.setdefault(head, {})
            for frame in attached:
                merged.setdefault(frame)
    fingerprint = primary.fingerprint.lower()
    key_id = primary.key_id.lower()
    heads = list(sections)
    parts = [_emitted_section(heads[0], list(sections[heads[0]]), fingerprint, key_id)]
    for tag in (_USER_ID_TAG, _USER_ATTRIBUTE_TAG, _PUBLIC_SUBKEY_TAG):
        section_heads = [head for head in heads[1:] if head[0] == tag]
        section_heads.sort(key=lambda head: _section_order(head, list(sections[head]), fingerprint, key_id))
        parts.extend(_emitted_section(head, list(sections[head]), fingerprint, key_id) for head in section_heads)
    data = b"".join(parts)
    openpgp.composed.SignedPublicKey.from_bytes(data)
    return _armored(data)


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
            can_sign=primary.algorithm.can_sign() and _declares_signing(_effective_self_signature(key, timestamp)),
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
                can_sign=subkey.key.algorithm.can_sign() and _declares_signing(binding_signature),
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
            can_sign=subkey.key.algorithm.can_sign() and _declares_signing(binding_signature),
            # Revoking the primary revokes everything beneath it, so a live subkey binding isn't enough
            revoked=primary_revoked or _subkey_is_revoked(primary, subkey),
        )

    if _issuer_is_primary(key, issuer_fingerprints, issuer_key_ids):
        return SigningKeyStatus(
            identified=True,
            fingerprint=key.fingerprint.lower(),
            expires=key_expires_at(key, at=at),
            can_sign=primary.algorithm.can_sign() and _declares_signing(_effective_self_signature(key, timestamp)),
            revoked=primary_revoked,
        )

    # Naming no key we hold is not the same as naming the primary, and a signature we can't attribute
    # is one we can't judge, so report it as such rather than borrowing the primary's answer
    return SigningKeyStatus(identified=False, fingerprint=None, expires=None, can_sign=False, revoked=False)


def _armored(data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    lines = [encoded[index : index + 64] for index in range(0, len(encoded), 64)]
    return "\n".join([_ARMOR_BEGIN, "", *lines, _ARMOR_END]) + "\n"


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
    active = [signature for signature in candidates if _signature_active(signature, timestamp)]
    if not active:
        return False
    latest = max(active, key=_revocation_preferring_order)
    return latest.typ() == _CERTIFICATION_REVOCATION_SIGNATURE_TYPE


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


def _certificate_groups(
    frames: list[tuple[int, bytes]],
) -> list[tuple[tuple[int, bytes], list[tuple[int, bytes]]]]:
    if (not frames) or (frames[0][0] != _PRIMARY_KEY_TAG):
        raise ValueError("The block does not begin with a primary key packet")
    groups = [(frames[0], [])]
    for frame in frames[1:]:
        if frame[0] == _PRIMARY_KEY_TAG:
            raise ValueError("The block holds more than one certificate")
        if frame[0] in (_USER_ID_TAG, _USER_ATTRIBUTE_TAG, _PUBLIC_SUBKEY_TAG):
            groups.append((frame, []))
        elif frame[0] not in _SKIPPABLE_PACKET_TAGS:
            groups[-1][1].append(frame)
    return groups


def _crc24(data: bytes) -> int:
    crc = 0xB704CE
    for octet in data:
        crc ^= octet << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def _dearmored(text: str) -> bytes:
    lines = []
    checksum = None
    in_data = False
    for line in text.splitlines()[1:]:
        stripped = line.strip()
        if not in_data:
            if stripped == "":
                in_data = True
            elif ":" not in stripped:
                in_data = True
                lines.append(stripped)
            continue
        if stripped.startswith("-----END"):
            break
        if stripped.startswith("="):
            checksum = stripped[1:5]
            continue
        lines.append(stripped)
    data = base64.b64decode("".join(lines), validate=True)
    if checksum is not None:
        declared = int.from_bytes(base64.b64decode(checksum, validate=True))
        if declared != _crc24(data):
            raise ValueError("Armor checksum mismatch")
    return data


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


def _earliest_self_certification(attached: list[tuple[int, bytes]], fingerprint: str, key_id: str) -> float:
    created_values = []
    for tag, body in attached:
        if tag != _SIGNATURE_TAG:
            continue
        signature = _parsed_signature(body)
        if (signature is None) or (signature.typ() not in _CERTIFICATION_SIGNATURE_TYPES):
            continue
        if not _signature_is_self(signature, fingerprint, key_id):
            continue
        created_values.append(signature.created() or 0)
    return min(created_values) if created_values else float("inf")


def _effective_self_signature(
    key: openpgp.composed.SignedPublicKey, timestamp: float
) -> openpgp.packet.Signature | None:
    direct = _latest_signature(_direct_self_signatures(key), timestamp)
    if key.version >= 6:
        return direct
    binding = _latest_signature(_binding_self_signatures(key, timestamp), timestamp)
    return binding if (binding is not None) else direct


def _emitted_section(
    head: tuple[int, bytes], attached: list[tuple[int, bytes]], fingerprint: str, key_id: str
) -> bytes:
    signatures: list[tuple[bytes, openpgp.packet.Signature]] = []
    opaques: list[tuple[int, bytes]] = []
    for tag, body in attached:
        signature = _parsed_signature(body) if (tag == _SIGNATURE_TAG) else None
        if signature is None:
            opaques.append((tag, body))
            continue
        if _local_certification(signature):
            continue
        signatures.append((body, signature))
    if head[0] == _PRIMARY_KEY_TAG:
        revocations = [pair for pair in signatures if pair[1].typ() == _KEY_REVOCATION_SIGNATURE_TYPE]
        others = [pair for pair in signatures if pair[1].typ() != _KEY_REVOCATION_SIGNATURE_TYPE]
        ordered = sorted(revocations, key=_signature_order) + sorted(others, key=_signature_order)
    else:
        ordered = sorted(signatures, key=_signature_order)
    frames = [head, *[(_SIGNATURE_TAG, body) for body, _ in ordered], *sorted(opaques)]
    return b"".join(_framed(tag, body) for tag, body in frames)


def _framed(tag: int, body: bytes) -> bytes:
    length = len(body)
    if length < 192:
        header = bytes((0xC0 | tag, length))
    elif length < 8384:
        header = bytes((0xC0 | tag, ((length - 192) >> 8) + 192, (length - 192) & 0xFF))
    else:
        header = bytes((0xC0 | tag, 0xFF)) + length.to_bytes(4)
    return header + body


def _frames(data: bytes) -> list[tuple[int, bytes]]:
    frames = []
    offset = 0
    while offset < len(data):
        ctb = data[offset]
        if not ctb & 0x80:
            raise ValueError(f"Invalid packet marker {ctb:#x} at offset {offset}")
        if ctb & 0x40:
            tag = ctb & 0x3F
            first = _octets(data, offset + 1, 1)[0]
            if first < 192:
                length, header = first, 2
            elif first < 224:
                length, header = ((first - 192) << 8) + _octets(data, offset + 2, 1)[0] + 192, 3
            elif first == 255:
                length, header = int.from_bytes(_octets(data, offset + 2, 4)), 6
            else:
                raise ValueError(f"Partial packet length at offset {offset}")
        else:
            tag = (ctb >> 2) & 0x0F
            kind = ctb & 0x03
            if kind == 3:
                raise ValueError(f"Indeterminate packet length at offset {offset}")
            sizes = {0: 1, 1: 2, 2: 4}[kind]
            length, header = int.from_bytes(_octets(data, offset + 1, sizes)), 1 + sizes
        body = data[offset + header : offset + header + length]
        if len(body) != length:
            raise ValueError(f"Truncated packet at offset {offset}")
        frames.append((tag, bytes(body)))
        offset += header + length
    return frames


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
    return max(valid, key=lambda signature: (signature.created() or 0, signature.to_bytes()))


def _local_certification(signature: openpgp.packet.Signature) -> bool:
    typ = signature.typ()
    if (typ not in _CERTIFICATION_SIGNATURE_TYPES) and (typ != _CERTIFICATION_REVOCATION_SIGNATURE_TYPE):
        return False
    return signature.exportable_certification() is False


def _octets(data: bytes, offset: int, count: int) -> bytes:
    taken = data[offset : offset + count]
    if len(taken) != count:
        raise ValueError(f"Truncated packet header at offset {offset}")
    return taken


def _parsed_signature(body: bytes) -> openpgp.packet.Signature | None:
    try:
        value = openpgp.packet.Packet.from_bytes(_framed(_SIGNATURE_TAG, body)).value
    except Exception:
        return None
    return value if isinstance(value, openpgp.packet.Signature) else None


def _primary_is_revoked(key: openpgp.composed.SignedPublicKey) -> bool:
    primary = key.primary_key
    for signature in key.details.revocation_signatures:
        if signature.typ() != _KEY_REVOCATION_SIGNATURE_TYPE:
            continue
        if _revocation_verifies(signature, primary):
            return True
    return False


def _revocation_preferring_order(signature: openpgp.packet.Signature) -> tuple[float, bool, bytes]:
    is_revocation = signature.typ() == _CERTIFICATION_REVOCATION_SIGNATURE_TYPE
    return (signature.created() or 0, is_revocation, signature.to_bytes())


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


def _section_order(
    head: tuple[int, bytes], attached: list[tuple[int, bytes]], fingerprint: str, key_id: str
) -> tuple[float, bytes]:
    tag, body = head
    if tag != _PUBLIC_SUBKEY_TAG:
        return (_earliest_self_certification(attached, fingerprint, key_id), body)
    try:
        value = openpgp.packet.Packet.from_bytes(_framed(tag, body)).value
    except Exception:
        return (float("inf"), body)
    if not isinstance(value, openpgp.packet.PublicSubkey):
        return (float("inf"), body)
    return (value.created_at, body)


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


def _signature_order(pair: tuple[bytes, openpgp.packet.Signature]) -> tuple[float, str, bytes]:
    body, signature = pair
    return (signature.created() or 0, signature.typ() or "", body)


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
