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
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
from typing import Final

import rfc8785

ALGORITHM: Final = "SLH-DSA-SHAKE-256s"
PUBLIC_PREFIX: Final = bytes.fromhex("3050300b060960864801650304031e034100")
SIGNATURE_SIZE: Final = 29792
VERSION: Final = "org.apache.tooling.atr.seal.v1"


def initialise(key_dir: pathlib.Path) -> bytes:
    key_dir.mkdir(mode=0o700)
    _sync_directory(key_dir.parent)
    return _generate(key_dir / "current.pem")


def publish(path: pathlib.Path, content: bytes) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o640)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(name, path)
    finally:
        os.unlink(name)
    _sync_directory(path.parent)


def seal(key_dir: pathlib.Path, data: pathlib.Path) -> None:
    target = data.with_suffix(".seal.json")
    if target.exists():
        raise FileExistsError(target)
    digest = _encode(_digest(data))
    successor = key_dir / "next.pem"
    record = {
        "datetime": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next": _encode(_generate(successor)),
        "sha3_512": digest,
        "v": VERSION,
    }
    current = key_dir / "current.pem"
    record["signature"] = _encode(_sign(current, rfc8785.dumps(record)))
    _verify_signature(_public(current), record)
    publish(target, rfc8785.dumps(record) + b"\n")
    os.replace(successor, current)
    _sync_directory(key_dir)


def verify(data: pathlib.Path, public: bytes) -> bytes:
    record = _read(data.with_suffix(".seal.json"))
    _verify_signature(public, record)
    if _encode(_digest(data)) != record["sha3_512"]:
        raise ValueError(f"Digest mismatch: {data}")
    return _decode(record["next"], 82)


def _decode(value: str, size: int) -> bytes:
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if (len(decoded) != size) or (_encode(decoded) != value):
        raise ValueError("Invalid base64url encoding or length")
    return decoded


def _digest(path: pathlib.Path) -> bytes:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha3_512").digest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _generate(path: pathlib.Path) -> bytes:
    private = _openssl("genpkey", "-algorithm", ALGORITHM)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(private)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(path.parent)
    return _public(path)


def _openssl(*arguments: str) -> bytes:
    result = subprocess.run(["openssl", *arguments], capture_output=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip() or "OpenSSL operation failed")
    return result.stdout


def _public(path: pathlib.Path) -> bytes:
    public = _openssl("pkey", "-in", str(path), "-pubout", "-outform", "DER")
    _validate_public(public)
    return public


def _read(path: pathlib.Path) -> dict[str, str]:
    content = path.read_bytes()
    record = json.loads(content)
    if (not isinstance(record, dict)) or (set(record) != {"datetime", "next", "sha3_512", "signature", "v"}):
        raise ValueError(f"Invalid seal fields: {path}")
    if not all(isinstance(value, str) for value in record.values()):
        raise ValueError(f"Seal fields must be strings: {path}")
    if (record["v"] != VERSION) or (rfc8785.dumps(record) + b"\n" != content):
        raise ValueError(f"Unsupported or noncanonical seal: {path}")
    instant = datetime.datetime.fromisoformat(record["datetime"])
    if (instant.tzinfo != datetime.UTC) or (
        instant.isoformat(timespec="seconds").replace("+00:00", "Z") != record["datetime"]
    ):
        raise ValueError(f"Invalid seal datetime: {path}")
    _validate_public(_decode(record["next"], 82))
    _decode(record["sha3_512"], 64)
    return record


def _sign(private: pathlib.Path, message: bytes) -> bytes:
    with tempfile.NamedTemporaryFile() as handle:
        handle.write(message)
        handle.flush()
        return _openssl("pkeyutl", "-sign", "-rawin", "-inkey", str(private), "-in", handle.name)


def _sync_directory(path: pathlib.Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_public(public: bytes) -> None:
    if (len(public) != 82) or (not public.startswith(PUBLIC_PREFIX)):
        raise ValueError(f"Expected a DER {ALGORITHM} public key")


def _verify_signature(public: bytes, record: dict[str, str]) -> None:
    _validate_public(public)
    message = {key: value for key, value in record.items() if key != "signature"}
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory)
        (path / "public.der").write_bytes(public)
        (path / "signature").write_bytes(_decode(record["signature"], SIGNATURE_SIZE))
        (path / "message").write_bytes(rfc8785.dumps(message))
        _openssl(
            "pkeyutl",
            "-verify",
            "-rawin",
            "-pubin",
            "-keyform",
            "DER",
            "-inkey",
            str(path / "public.der"),
            "-sigfile",
            str(path / "signature"),
            "-in",
            str(path / "message"),
        )
