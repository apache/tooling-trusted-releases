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

import json
import pathlib
import shutil
import stat
import subprocess
import unittest.mock as mock

import pytest
import rfc8785

import atr.sealing as sealing

pytestmark = pytest.mark.skipif(
    (shutil.which("openssl") is None)
    or (
        sealing.ALGORITHM.encode()
        not in subprocess.run(["openssl", "list", "-signature-algorithms"], capture_output=True, check=False).stdout
    ),
    reason="OpenSSL with SLH-DSA-SHAKE-256s is required",
)


def test_failed_signature_refuses(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keys = tmp_path / "keys"
    root = sealing.initialise(keys)
    data = tmp_path / "1.jsonl"
    data.write_bytes(b"{}\n")
    monkeypatch.setattr(sealing, "_sign", mock.Mock(return_value=bytes(sealing.SIGNATURE_SIZE)))
    with pytest.raises(ValueError):
        sealing.seal(keys, data)
    assert not data.with_suffix(".seal.json").exists()
    assert sealing._public(keys / "current.pem") == root
    with pytest.raises(FileExistsError):
        sealing.seal(keys, data)


@pytest.mark.parametrize("operation", ["link", "replace"])
def test_interrupted_round_refuses(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    keys = tmp_path / "keys"
    sealing.initialise(keys)
    first, second = tmp_path / "1.jsonl", tmp_path / "2.jsonl"
    first.write_bytes(b"{}\n")
    second.write_bytes(b"{}\n")
    with monkeypatch.context() as failure:
        failure.setattr(sealing.os, operation, mock.Mock(side_effect=OSError("interrupted")))
        with pytest.raises(OSError, match="interrupted"):
            sealing.seal(keys, first)
    expected = {first, second}
    if operation == "replace":
        expected.add(first.with_suffix(".seal.json"))
    assert set(tmp_path.glob("*.json*")) == expected
    with pytest.raises(FileExistsError):
        sealing.seal(keys, second)
    assert not second.with_suffix(".seal.json").exists()


def test_openssl_error_details() -> None:
    with pytest.raises(ValueError, match="atr-sealing-invalid-command"):
        sealing._openssl("atr-sealing-invalid-command")


def test_seal_chain(tmp_path: pathlib.Path) -> None:
    keys = tmp_path / "keys"
    root = sealing.initialise(keys)
    first, second = tmp_path / "1.jsonl", tmp_path / "2.jsonl"
    for data, content in ((first, b""), (second, b"{}\n")):
        data.write_bytes(content)
        sealing.seal(keys, data)
        assert stat.S_IMODE(data.with_suffix(".seal.json").stat().st_mode) == 0o640
    assert {path.name for path in keys.iterdir()} == {"current.pem"}
    assert stat.S_IMODE(keys.stat().st_mode) == 0o700
    assert stat.S_IMODE((keys / "current.pem").stat().st_mode) == 0o400
    public = sealing.verify(first, root)
    sealing.verify(second, public)
    with pytest.raises(ValueError):
        sealing.verify(second, root)
    second.write_bytes(b"changed\n")
    with pytest.raises(ValueError, match="Digest mismatch"):
        sealing.verify(second, public)
    path = first.with_suffix(".seal.json")
    record = json.loads(path.read_bytes())
    record["next"] = sealing._encode(public[:-1] + bytes([public[-1] ^ 1]))
    path.write_bytes(rfc8785.dumps(record) + b"\n")
    with pytest.raises(ValueError):
        sealing.verify(first, root)


def test_verify_requires_canonical_encoding(tmp_path: pathlib.Path) -> None:
    root = sealing.initialise(tmp_path / "keys")
    data = tmp_path / "1.jsonl"
    data.write_bytes(b"{}\n")
    sealing.seal(tmp_path / "keys", data)
    path = data.with_suffix(".seal.json")
    content = path.read_bytes()
    record = json.loads(content)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    signature = record["signature"]
    pad_bits = signature[:-1] + alphabet[alphabet.index(signature[-1]) + 1]
    for change, error in [
        ({"v": sealing.VERSION + ".unknown"}, "Unsupported"),
        ({"extra": "field"}, "Invalid seal fields"),
        ({"datetime": 0}, "Seal fields must be strings"),
        ({"datetime": "2026-09-28T12:34:56+00:00"}, "Invalid seal datetime"),
        ({"datetime": "2026-09-28T12:34:56.000Z"}, "Invalid seal datetime"),
        ({"next": sealing._encode(bytes(82))}, "Expected a DER"),
        ({"sha3_512": sealing._encode(bytes(63))}, "Invalid base64url"),
        ({"signature": signature + "="}, "Invalid base64url"),
        ({"signature": pad_bits}, "Invalid base64url"),
        ({"signature": "!" + signature}, "Only base64 data"),
    ]:
        path.write_bytes(rfc8785.dumps(record | change) + b"\n")
        with pytest.raises(ValueError, match=error):
            sealing.verify(data, root)
    for altered in (
        b" " + content,
        content.rstrip(b"\n"),
        b'{"v":' + json.dumps(record["v"]).encode() + b"," + content[1:],
    ):
        path.write_bytes(altered)
        with pytest.raises(ValueError, match="noncanonical"):
            sealing.verify(data, root)
