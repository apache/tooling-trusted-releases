#!/usr/bin/env python3
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

# Usage: uv run --frozen python3 scripts/hints_import.py [HINTS_FILE]

import asyncio
import base64
import contextlib
import hashlib
import itertools
import pathlib
import re
import sys

sys.path.append(".")

import openpgp
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.util as util

ARMOR_PATTERN = re.compile(rb"-----BEGIN PGP PUBLIC KEY BLOCK-----.*?-----END PGP [A-Z ]+-----", re.S)
BASE64_LINE_PATTERN = re.compile(rb"[A-Za-z0-9+/=]+")
BATCH_SIZE = 500
HEX_DIGITS = frozenset("0123456789abcdef")
HINT_LENGTHS = frozenset({16, 32, 40, 64})


async def amain(hints: set[str]) -> None:
    await db.init_database_for_worker()
    await seed(hints)


def dearmor(block: bytes) -> bytes:
    lines = []
    for raw in block.splitlines():
        line = raw.strip()
        if (not line) or (b":" in line) or (BASE64_LINE_PATTERN.fullmatch(line) is None):
            continue
        if line.startswith(b"=") and (len(line) == 5):
            continue
        lines.append(line)
    joined = b"".join(lines)
    joined += b"=" * (-len(joined) % 4)
    return base64.b64decode(joined)


def iter_packets(data: bytes):
    off = 0
    while off < len(data):
        first = data[off]
        if (first & 0x80) == 0:
            raise ValueError(f"bad packet header byte {first:#04x} at offset {off}")
        off += 1
        if first & 0x40:
            tag = first & 0x3F
            body, off = read_new_format_body(data, off)
        else:
            tag = (first >> 2) & 0x0F
            body, off = read_old_format_body(data, off, first & 0x03)
        yield tag, body


def key_packet_ids(body: bytes) -> set[str]:
    version = body[0]
    if version in (2, 3):
        modulus, off = mpi_at(body, 8)
        exponent, _ = mpi_at(body, off)
        fingerprint = hashlib.md5(modulus + exponent, usedforsecurity=False).hexdigest()
        return {fingerprint, modulus[-8:].hex()}
    if version == 4:
        digest = hashlib.sha1(b"\x99" + len(body).to_bytes(2, "big") + body, usedforsecurity=False).hexdigest()
        return {digest, digest[-16:]}
    if version in (5, 6):
        prefix = b"\x9a" if version == 5 else b"\x9b"
        digest = hashlib.sha256(prefix + len(body).to_bytes(4, "big") + body).hexdigest()
        return {digest, digest[:16]}
    raise ValueError(f"unknown key packet version {version}")


def lenient_member_ids(armored: str) -> set[str]:
    key, _ = openpgp.PublicKey.from_armor(armored)
    return util.openpgp_member_ids(key)


def load_hints(path: pathlib.Path) -> set[str]:
    hints = set()
    for token in path.read_text(encoding="utf-8").split():
        hint = token.lower()
        if (len(hint) not in HINT_LENGTHS) or (not (set(hint) <= HEX_DIGITS)):
            raise ValueError(f"Invalid hint: {token!r}")
        hints.add(hint)
    return hints


def main() -> None:
    default_path = pathlib.Path(__file__).parent / "signature_hints.txt"
    hints_path = pathlib.Path(sys.argv[1]) if (len(sys.argv) > 1) else default_path
    asyncio.run(amain(load_hints(hints_path)))


def mpi_at(body: bytes, off: int) -> tuple[bytes, int]:
    bits = int.from_bytes(body[off : off + 2], "big")
    size = (bits + 7) // 8
    return body[off + 2 : off + 2 + size], off + 2 + size


def read_new_format_body(data: bytes, off: int) -> tuple[bytes, int]:
    chunks = []
    while True:
        octet = data[off]
        off += 1
        partial = False
        if octet < 192:
            size = octet
        elif octet < 224:
            size = ((octet - 192) << 8) + data[off] + 192
            off += 1
        elif octet == 255:
            size = int.from_bytes(data[off : off + 4], "big")
            off += 4
        else:
            size = 1 << (octet & 0x1F)
            partial = True
        chunks.append(data[off : off + size])
        off += size
        if not partial:
            return b"".join(chunks), off


def read_old_format_body(data: bytes, off: int, length_type: int) -> tuple[bytes, int]:
    if length_type == 3:
        return data[off:], len(data)
    size_bytes = (1, 2, 4)[length_type]
    size = int.from_bytes(data[off : off + size_bytes], "big")
    off += size_bytes
    return data[off : off + size], off + size


async def seed(hints: set[str]) -> None:
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        keys = await data.signing_certificate(deleted=db.NOT_SET).all()
        covered: set[str] = set()
        flagged: list[str] = []
        failed = 0
        for key in keys:
            armored = key.ascii_armored_key
            if isinstance(armored, bytes):
                armored = armored.decode("utf-8", errors="replace")
            ids: set[str] = set()
            with contextlib.suppress(Exception):
                ids.update(strict_member_ids(armored.encode("utf-8", errors="replace")))
            with contextlib.suppress(Exception):
                ids.update(lenient_member_ids(armored))
            if not ids:
                failed += 1
                print(f"unparseable key {key.fingerprint}")
                continue
            covered.update(ids)
            if (ids & hints) and (not key.historic_use):
                flagged.append(key.fingerprint)
        for batch in itertools.batched(sorted(flagged), BATCH_SIZE):
            await data.execute(
                sqlmodel.update(sql.SigningCertificate)
                .where(via(sql.SigningCertificate.fingerprint).in_(batch))
                .values(historic_use=True)
            )
        existing = {row.hint for row in await data.signature_hint().all()}
        stale = sorted(existing & covered)
        for batch in itertools.batched(stale, BATCH_SIZE):
            await data.execute(sqlmodel.delete(sql.SignatureHint).where(via(sql.SignatureHint.hint).in_(batch)))
        missing = sorted(hints - covered - existing)
        for batch in itertools.batched(missing, BATCH_SIZE):
            data.add_all([sql.SignatureHint(hint=hint) for hint in batch])
        await data.commit()
    print(f"keys: {len(keys)}")
    print(f"unparseable keys: {failed}")
    print(f"keys newly flagged historic_use: {len(flagged)}")
    print(f"stale hints deleted: {len(stale)}")
    print(f"uncovered hints inserted: {len(missing)}")


def strict_member_ids(armored: bytes) -> set[str]:
    ids: set[str] = set()
    error: Exception | None = None
    for block in ARMOR_PATTERN.findall(armored):
        try:
            payload = dearmor(block)
            for tag, body in iter_packets(payload):
                if tag in (6, 14):
                    ids.update(key_packet_ids(body))
        except Exception as e:
            error = e
    if not ids:
        raise error or ValueError("no key packets found")
    return ids


if __name__ == "__main__":
    main()
