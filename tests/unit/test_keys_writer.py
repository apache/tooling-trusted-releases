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

import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.models.safe as safe
import atr.storage.outcome as outcome
import atr.storage.writers.keys as keys_writer


class Query:
    def __init__(self, value):
        self._value = value

    async def get(self):
        return self._value

    async def demand(self, error: Exception):
        if self._value is None:
            raise error
        return self._value


class MockData:
    def __init__(self, key, committees_after_commit: dict[str, object]):
        self._key = key
        self._committees_after_commit = committees_after_commit
        self.begin_immediate = mock.AsyncMock()
        self.commit = mock.AsyncMock()
        self.delete = mock.AsyncMock()
        self.execute = mock.AsyncMock()

    def public_signing_key(self, **_kwargs):
        return Query(self._key)

    def committee(self, *, key: str, _public_signing_keys: bool = False):
        assert _public_signing_keys is True
        return Query(self._committees_after_commit[key])


@pytest.mark.asyncio
async def test_delete_key_removal_deletes_empty_keys_file(tmp_path):
    owned_key = SimpleNamespace(
        fingerprint="fp1",
        committees=[SimpleNamespace(key="alpha")],
    )
    data = MockData(
        owned_key,
        committees_after_commit={"alpha": _committee("alpha", [])},
    )
    writer, _write = _make_foundation_committer(data)

    keys_path = tmp_path / "alpha" / "KEYS"
    keys_path.parent.mkdir(parents=True)
    keys_path.write_text("stale content", encoding="utf-8")

    with (
        mock.patch.object(keys_writer.paths, "get_downloads_dir", return_value=tmp_path),
        mock.patch.object(keys_writer.util, "chmod_directories"),
    ):
        result = await writer.delete_key("fp1")

    assert isinstance(result, outcome.Result)
    assert not keys_path.exists()
    data.delete.assert_awaited_once_with(owned_key)
    data.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_committee_associations_removal_deletes_empty_keys_file(tmp_path):
    owned_key = SimpleNamespace(fingerprint="fp1", committees=[SimpleNamespace(key="alpha")])
    data = MockData(
        owned_key,
        committees_after_commit={"alpha": _committee("alpha", [])},
    )
    writer, write = _make_foundation_committer(data)

    keys_path = tmp_path / "alpha" / "KEYS"
    keys_path.parent.mkdir(parents=True)
    keys_path.write_text("stale content", encoding="utf-8")

    with (
        mock.patch.object(keys_writer.paths, "get_downloads_dir", return_value=tmp_path),
        mock.patch.object(keys_writer.util, "chmod_directories"),
    ):
        affected = await writer.update_committee_associations("fp1", [])

    assert affected == {"alpha"}
    assert not keys_path.exists()
    assert write.as_committee_participant.call_count == 0
    data.begin_immediate.assert_awaited_once()
    data.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_committee_associations_removal_rewrites_keys_file_with_remaining_keys(tmp_path):
    temp_dir = safe.StatePath(tmp_path)
    owned_key = SimpleNamespace(fingerprint="fp1", committees=[SimpleNamespace(key="alpha")])
    remaining_key = _public_key("bbbbccccdddd1111")
    data = MockData(
        owned_key,
        committees_after_commit={"alpha": _committee("alpha", [remaining_key])},
    )
    writer, write = _make_foundation_committer(data)

    keys_path = temp_dir / "alpha" / "KEYS"
    keys_path.parent.path.mkdir(parents=True)
    keys_path.path.write_text("stale content", encoding="utf-8")

    with (
        mock.patch.object(keys_writer.paths, "get_downloads_dir", return_value=temp_dir),
        mock.patch.object(keys_writer.util, "chmod_directories"),
    ):
        affected = await writer.update_committee_associations("fp1", [])

    assert affected == {"alpha"}
    assert keys_path.path.exists()
    content = keys_path.path.read_text(encoding="utf-8")
    assert "stale content" not in content
    assert remaining_key.fingerprint.upper() in content
    assert "Signing keys for the alpha committee" in content
    assert write.as_committee_participant.call_count == 0


def _committee(key: str, public_signing_keys: list[object], *, is_podling: bool = False):
    return SimpleNamespace(
        key=key,
        is_podling=is_podling,
        public_signing_keys=public_signing_keys,
    )


def _make_foundation_committer(data: MockData):
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    write.as_committee_participant = mock.MagicMock()
    write_as = mock.MagicMock()
    return keys_writer.FoundationCommitter(write, write_as, data), write


def _public_key(
    fingerprint: str,
    *,
    apache_uid: str = "bob",
    primary_declared_uid: str = "Bob <bob@example.org>",
    ascii_armored_key: str | None = None,
):
    if ascii_armored_key is None:
        ascii_armored_key = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nbody\n-----END PGP PUBLIC KEY BLOCK-----\n"
    return SimpleNamespace(
        fingerprint=fingerprint,
        apache_uid=apache_uid,
        primary_declared_uid=primary_declared_uid,
        ascii_armored_key=ascii_armored_key,
    )
