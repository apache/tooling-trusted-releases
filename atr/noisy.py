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

import hmac
import secrets
from typing import Any, Final, Literal, NewType, TypeGuard

# This file is intended for standalone use
# It follows the order of the Noisy Secrets specification
# Therefore it breaks some ATR coding conventions

# Constants not based on custom types

BASE37_ALPHABET: Final[bytes] = b"0123456789_abcdefghijklmnopqrstuvwxyz"
BASE36_ALPHABET: Final[bytes] = b"0123456789abcdefghijklmnopqrstuvwxyz"
BASE32_ALPHABET: Final[bytes] = b"23456789abcdefghijkmnpqrstuvwxyz"
COMPONENT_ALPHABET: Final[bytes] = b"-0123456789abcdefghijklmnopqrstuvwxyz"

BASE37_ALPHABET_SET: Final[frozenset[int]] = frozenset(BASE37_ALPHABET)
BASE36_ALPHABET_SET: Final[frozenset[int]] = frozenset(BASE36_ALPHABET)
BASE32_ALPHABET_SET: Final[frozenset[int]] = frozenset(BASE32_ALPHABET)
COMPONENT_ALPHABET_SET: Final[frozenset[int]] = frozenset(COMPONENT_ALPHABET)

FIELD_MAP: Final[dict[int, int]] = {c: i for i, c in enumerate(BASE37_ALPHABET)}
Q: Final[Literal[37]] = 37

DOT: Final[Literal[b"."]] = b"."
EMPTY: Final[Literal[b""]] = b""
HYPHEN: Final[Literal[b"-"]] = b"-"
TWO: Final[Literal[b"2"]] = b"2"
UNDERSCORE: Final[Literal[b"_"]] = b"_"

# Constants used in tests only

K: Final[Literal[32]] = 32

# Custom types

type Prefix = Literal[b"secret"]
type Pad = Literal[b"_"]

NamespaceString = NewType("NamespaceString", bytes)
PayloadString = NewType("PayloadString", bytes)
InterleavedChecksumString = NewType("InterleavedChecksumString", bytes)
NoisySecretString = NewType("NoisySecretString", bytes)
PaddedNamespaceTag = NewType("PaddedNamespaceTag", bytes)
MessageTag = NewType("MessageTag", bytes)
EvenMessageTag = NewType("EvenMessageTag", MessageTag)
OddMessageTag = NewType("OddMessageTag", MessageTag)
ChecksumTag = NewType("ChecksumTag", bytes)
InterleavedChecksumTag = NewType("InterleavedChecksumTag", bytes)
NoisySecretTag = NewType("NoisySecretTag", bytes)

Namespace = NewType("Namespace", NamespaceString)
Payload = NewType("Payload", PayloadString)
NoisySecret = NewType("NoisySecret", NoisySecretTag)
PaddedNamespace = NewType("PaddedNamespace", PaddedNamespaceTag)
Message = NewType("Message", MessageTag)
EvenMessage = NewType("EvenMessage", EvenMessageTag)
OddMessage = NewType("OddMessage", OddMessageTag)
Checksum = NewType("Checksum", ChecksumTag)
InterleavedChecksum = NewType("InterleavedChecksum", InterleavedChecksumTag)
Candidate = NewType("Candidate", bytes)
CandidateNamespace = NewType("CandidateNamespace", NamespaceString)
CandidatePayload = NewType("CandidatePayload", PayloadString)
ExpectedCandidate = NewType("ExpectedCandidate", NoisySecretTag)

FQDN = NewType("FQDN", bytes)

# Constants based on custom types

PREFIX: Final[Prefix] = b"secret"
PAD: Final[Pad] = b"_"


def construct_namespace(fqdn: FQDN | None) -> Namespace:
    if fqdn is None:
        return Namespace(NamespaceString(TWO))
    components = fqdn.split(DOT)
    for component in components:
        if is_component_string(component) is False:
            raise ValueError(f"Invalid component: {component}")
        if component.startswith(HYPHEN) or component.endswith(HYPHEN):
            raise ValueError(f"Component cannot start or end with a hyphen: {component}")
        if component == EMPTY:
            raise ValueError(f"Component cannot be empty: {component}")
    reversed_components = list(reversed(components))
    for i, component in enumerate(reversed_components):
        reversed_components[i] = component.replace(HYPHEN, UNDERSCORE + UNDERSCORE)
    joined = UNDERSCORE.join(reversed_components)
    joined_length = len(joined)
    if joined_length > 30:
        raise ValueError(f"Namespace is too long: {joined}")
    length = bytes([BASE32_ALPHABET[joined_length + 1]])
    return Namespace(NamespaceString(length + PAD + joined))


def construct_namespace_domain(namespace: Namespace) -> FQDN | None:
    if namespace == Namespace(NamespaceString(TWO)):
        return None
    suffix = namespace[2:]
    suffix = suffix.replace(UNDERSCORE + UNDERSCORE, HYPHEN)
    # TODO: Specification says Pad, but this isn't really Pad
    suffix_components = suffix.split(PAD)
    reversed_suffix_components = list(reversed(suffix_components))
    fqdn_bytes = DOT.join(reversed_suffix_components)
    return FQDN(fqdn_bytes)


def construct_padded_namespace_tag(namespace_string: NamespaceString) -> PaddedNamespaceTag:
    length = len(namespace_string)
    padding = PAD * (32 - length)
    return PaddedNamespaceTag(namespace_string + padding)


def construct_padded_namespace(namespace: Namespace) -> PaddedNamespace:
    return PaddedNamespace(construct_padded_namespace_tag(namespace))


def construct_payload() -> Payload:
    payload = []
    for i in range(32):
        payload.append(secrets.choice(BASE32_ALPHABET))
    return Payload(PayloadString(bytes(payload)))


def construct_message_tags(
    namespace_string: NamespaceString, payload_string: PayloadString
) -> tuple[EvenMessageTag, OddMessageTag]:
    padded_namespace_tag = construct_padded_namespace_tag(namespace_string)
    even_message_tag = EvenMessageTag(MessageTag(padded_namespace_tag[::2] + payload_string[::2]))
    odd_message_tag = OddMessageTag(MessageTag(padded_namespace_tag[1::2] + payload_string[1::2]))
    return (even_message_tag, odd_message_tag)


def construct_messages(namespace: Namespace, payload: Payload) -> tuple[EvenMessage, OddMessage]:
    even_message_tag, odd_message_tag = construct_message_tags(namespace, payload)
    return (EvenMessage(even_message_tag), OddMessage(odd_message_tag))


def construct_checksum_tag(message_tag: MessageTag) -> ChecksumTag:
    return ChecksumTag(checksum_compute(message_tag))


def construct_checksum(message: Message) -> Checksum:
    return Checksum(construct_checksum_tag(message))


def construct_interleaved_checksum_tag(
    even_checksum_tag: ChecksumTag, odd_checksum_tag: ChecksumTag
) -> InterleavedChecksumTag:
    interleaved_checksum_tag = InterleavedChecksumTag(
        bytes(b for pair in zip(even_checksum_tag, odd_checksum_tag) for b in pair)
    )
    return InterleavedChecksumTag(interleaved_checksum_tag)


def construct_interleaved_checksum(even_checksum: Checksum, odd_checksum: Checksum) -> InterleavedChecksum:
    return InterleavedChecksum(construct_interleaved_checksum_tag(even_checksum, odd_checksum))


def construct_noisy_secret_tag(namespace_string: NamespaceString, payload_string: PayloadString) -> NoisySecretTag:
    even_message_tag, odd_message_tag = construct_message_tags(namespace_string, payload_string)
    even_checksum_tag = construct_checksum_tag(even_message_tag)
    odd_checksum_tag = construct_checksum_tag(odd_message_tag)
    interleaved_checksum_tag = construct_interleaved_checksum_tag(even_checksum_tag, odd_checksum_tag)
    return NoisySecretTag(PREFIX + PAD + namespace_string + PAD + payload_string + interleaved_checksum_tag)


def construct_noisy_secret(fqdn: FQDN | None) -> NoisySecret:
    namespace = construct_namespace(fqdn)
    payload = construct_payload()
    noisy_secret_tag = construct_noisy_secret_tag(namespace, payload)
    return NoisySecret(noisy_secret_tag)


def is_candidate(value: Any) -> TypeGuard[Candidate]:
    if not isinstance(value, bytes):
        return False
    if len(value) != 49 and not (51 <= len(value) <= 80):
        return False
    return True


def construct_candidate_namespace(candidate: Candidate) -> CandidateNamespace | None:
    candidate_length = len(candidate)
    namespace_range = candidate[7 : candidate_length - 41]
    if not is_namespace_string(namespace_range):
        return None
    return CandidateNamespace(NamespaceString(namespace_range))


def construct_candidate_payload(candidate: Candidate) -> CandidatePayload | None:
    candidate_length = len(candidate)
    payload_range = candidate[candidate_length - 40 : candidate_length - 8]
    if not is_payload_string(payload_range):
        return None
    return CandidatePayload(PayloadString(payload_range))


def construct_expected_candidate(candidate: Candidate) -> ExpectedCandidate | None:
    candidate_namespace = construct_candidate_namespace(candidate)
    if candidate_namespace is None:
        return None
    candidate_payload = construct_candidate_payload(candidate)
    if candidate_payload is None:
        return None
    expected_candidate = construct_noisy_secret_tag(candidate_namespace, candidate_payload)
    return ExpectedCandidate(expected_candidate)


def is_noisy_secret_tag(candidate: Candidate) -> TypeGuard[NoisySecretTag]:
    expected_candidate = construct_expected_candidate(candidate)
    if expected_candidate is None:
        return False
    return hmac.compare_digest(candidate, expected_candidate)


def checksum_compute(message: bytes | bytearray) -> bytes:
    r0 = r1 = r2 = r3 = 0
    for b in message:
        feedback = (FIELD_MAP[b] + r0) % Q
        r0 = (r1 - feedback * 6) % Q
        r1 = (r2 - feedback * 26) % Q
        r2 = (r3 - feedback * 21) % Q
        r3 = (-feedback * 3) % Q
    return bytes(
        [
            BASE37_ALPHABET[(-r0) % Q],
            BASE37_ALPHABET[(-r1) % Q],
            BASE37_ALPHABET[(-r2) % Q],
            BASE37_ALPHABET[(-r3) % Q],
        ]
    )


def is_component_string(value: bytes) -> bool:
    return frozenset(value) <= COMPONENT_ALPHABET_SET


def is_namespace_string(value: bytes) -> bool:
    value_length = len(value)
    if value_length == 0:
        return False
    length_prefix = value[0:1]
    i = BASE32_ALPHABET.find(length_prefix)
    if i in (-1, 1):
        return False
    if value_length == 1:
        return (length_prefix == TWO) and (i == 0)
    if value_length != (i + 1):
        return False
    if value[1:2] != UNDERSCORE:
        return False
    return is_namespace_string_suffix(value[2:])


def is_namespace_string_suffix(suffix: bytes) -> bool:
    suffix_length = len(suffix)
    first = suffix[0]
    if first not in BASE36_ALPHABET_SET:
        return False
    if suffix_length == 1:
        return True
    last = suffix[-1]
    if last not in BASE36_ALPHABET_SET:
        return False
    if suffix_length == 2:
        return True
    middle = suffix[1:-1]
    return frozenset(middle) <= BASE37_ALPHABET_SET


def is_payload_string(value: bytes) -> bool:
    return frozenset(value) <= BASE32_ALPHABET_SET


def create(fqdn: bytes | None = None) -> NoisySecret:
    noisy_secret = construct_noisy_secret(FQDN(fqdn) if (fqdn is not None) else None)
    return NoisySecret(noisy_secret)


def verify(bytes_value: bytes) -> bool:
    if not is_candidate(bytes_value):
        return False
    return is_noisy_secret_tag(bytes_value)
