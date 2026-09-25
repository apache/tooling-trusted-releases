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
import functools
import hashlib
import io
import itertools

import openpgp
import pytest

import atr.pgp as pgp
import tests.unit.pgp_fixtures as pgp_fixtures


def _assert_written(data: bytes) -> None:
    for checksum in (False, True):
        output = io.StringIO()
        openpgp.armor.write(data, openpgp.armor.BlockType.PublicKey, output, include_checksum=checksum)
        assert output.getvalue() == _expected_armor(data, checksum), (len(data), data[:16].hex(), checksum)


def _crc24(data: bytes) -> int:
    table = _crc24_table()
    crc = 0xB704CE
    for octet in data:
        crc = ((crc << 8) & 0xFFFFFF) ^ table[(crc >> 16) ^ octet]
    return crc


@functools.cache
def _crc24_table() -> tuple[int, ...]:
    table = []
    for octet in range(256):
        crc = octet << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
        table.append(crc)
    return tuple(table)


def _expected_armor(data: bytes, checksum: bool) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    body = "".join(encoded[offset : offset + 64] + "\n" for offset in range(0, len(encoded), 64))
    footer = "=" + base64.b64encode(_crc24(data).to_bytes(3)).decode("ascii") + "\n" if checksum else ""
    return "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n" + body + footer + "-----END PGP PUBLIC KEY BLOCK-----\n"


def _packet(tag: int, body: bytes, form: str) -> bytes:
    size = len(body)
    if form.startswith("old"):
        width = int(form[-1])
        return bytes([0x80 | (tag << 2) | {1: 0, 2: 1, 4: 2}[width]]) + size.to_bytes(width) + body
    if form == "new1":
        return bytes([0xC0 | tag, size]) + body
    if form == "new2":
        return bytes([0xC0 | tag, ((size - 192) >> 8) + 192, (size - 192) & 255]) + body
    return bytes([0xC0 | tag, 255]) + size.to_bytes(4) + body


def test_armor_reference_known_answers() -> None:
    assert _crc24(b"") == 0xB704CE
    assert _crc24(b"123456789") == 0x21CF02
    assert _expected_armor(b"123456789", True) == (
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nMTIzNDU2Nzg5\n=Ic8C\n-----END PGP PUBLIC KEY BLOCK-----\n"
    )


@pytest.mark.parametrize("length", [0, 1, 2])
def test_armor_write_preserves_every_short_byte_string(length: int) -> None:
    for value in range(256**length):
        _assert_written(value.to_bytes(length))


def test_armor_write_preserves_length_boundaries_and_patterns() -> None:
    boundaries = (48, 96, 192, 256, 768, 1024, 1536, 2048, 4096, 8192, 65536)
    lengths = sorted({boundary + delta for boundary in boundaries for delta in range(-2, 3)})
    size = max(lengths)
    patterns = (bytes(size), b"\xff" * size, (bytes(range(256)) * ((size + 255) // 256))[:size])
    patterns += (hashlib.shake_256(b"ATR armor regression").digest(size),)
    for pattern in patterns:
        for length in lengths:
            _assert_written(pattern[:length])


def test_armor_write_preserves_megabyte_payload() -> None:
    _assert_written(hashlib.shake_256(b"ATR large armor regression").digest(1024 * 1024 + 1))


def test_armor_write_preserves_streaming_lengths() -> None:
    data = hashlib.shake_256(b"ATR streaming armor regression").digest(1600)
    for length in range(len(data) + 1):
        _assert_written(data[:length])


@pytest.mark.parametrize("version", [2, 3, 4, 5, 6])
def test_armored_preserves_packet_headers_and_lengths(version: int) -> None:
    lengths = {
        "old1": (0, 1, 191, 192, 255),
        "old2": (0, 1, 255, 256, 65535),
        "old4": (0, 1, 255, 256, 65535, 65536),
        "new1": (0, 1, 191),
        "new2": (192, 193, 8383),
        "new5": (0, 1, 191, 192, 8383, 8384, 65535, 65536),
    }
    payload = bytes(range(256)) * 256
    for form, sizes in lengths.items():
        primary = _packet(6, bytes([version]) + bytes(191 if form == "new2" else 0), form)
        for size in sizes:
            data = primary + _packet(13, payload[:size], form)
            assert pgp._armored(data) == _expected_armor(data, version != 6), (version, form, size)


@pytest.mark.parametrize("version", [4, 6])
def test_armored_preserves_packet_tags_order_and_duplicates(version: int) -> None:
    primary = b"\xc6\x01" + bytes([version])
    for form in ("old1", "old2", "old4", "new1", "new2", "new5"):
        body = bytes([version]) + bytes(191 if form == "new2" else 0)
        packets = [_packet(tag, body, form) for tag in range(16 if form.startswith("old") else 64)]
        data = primary + b"".join(packets[::-1] + packets + packets)
        assert pgp._armored(data) == _expected_armor(data, version != 6), (version, form)
    signature = b"\xc2\x02\x00\xff"
    packets = (primary, b"\xb5\x00\x01a", b"\xcd\xff\x00\x00\x00\x01b", signature, signature)
    for order in sorted(set(itertools.permutations(packets))):
        data = b"".join(order)
        assert pgp._armored(data) == _expected_armor(data, version != 6), (version, order)


def test_certificate_rearmored_matches_independent_armor() -> None:
    certificates = (
        pgp_fixtures.ALL_UIDS_REVOKED_PUBLIC_KEY_ASC,
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC,
        pgp_fixtures.LOCAL_CERTIFICATION_PUBLIC_KEY_ASC,
        pgp_fixtures.RECERTIFIED_UID_PUBLIC_KEY_ASC,
        pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC,
        pgp_fixtures.REVOKED_PRIMARY_UID_PUBLIC_KEY_ASC,
        pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC,
        pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC,
        pgp_fixtures.RFC9580_V6_PUBLIC_KEY_ASC,
    )
    for certificate in certificates:
        lines = certificate.splitlines()
        encoded = "".join(line for line in lines[lines.index("") + 1 :] if not line.startswith(("=", "-----")))
        data = base64.b64decode(encoded, validate=True)
        expected = _expected_armor(data, certificate != pgp_fixtures.RFC9580_V6_PUBLIC_KEY_ASC)
        if any(line.startswith("=") for line in lines) or (certificate == pgp_fixtures.RFC9580_V6_PUBLIC_KEY_ASC):
            assert expected == certificate
        assert pgp.certificate_rearmored(certificate) == expected
        assert pgp.certificate_rearmored(expected) == expected
