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

import datetime
import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.models.safe as safe
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.storage.writers.keys as keys_writer

_EMBEDDED_V4_EXPIRING_KEY_ASC = """-----BEGIN PGP PUBLIC KEY BLOCK-----
Version: GnuPG v1.4.10 (GNU/Linux)

mI0ES+OoSQEEAJUZ/+fC6DXN2X7Wxl4Huud/+i2qP1hcq+Qnbr7hVCKEnn0edYl+
6xfsKmAMBjl+qTZxPSDSx4r3ciMiIbnvXFtlBAQmji86kqoR6fm9s8BN7LTq7+2/
c2FHVF67D7zES7WgHc4i7CfiZnwXgkLvi5b1jBt+MTAOrFhdobxoy6/XABEBAAGI
twQfAQIAIQUCS+OsRRcMgAEO5b6XkoLYC591QPHM0u2U0hc56QIHAAAKCRA0t9EL
wQjoOrRXBACBqhigTcj8pJY14AkjV+ZzUbm55kJRDPdU7NQ1PSvczm7HZaL3b8Lr
Psa5c5+caVLjsGWkQycQl7lUIGU84KoUfwACQKVVLkqJz8LkL54lLcwkG70+1NH5
xoSNcHHVbYtqDLNeCOq5jEIoXuz44wiWVEfF+/B115PvgwZ63pjH1rRGVGVzdCBL
ZXkgRGVtb25zdHJhdGluZyBSZXZva2VyIFRyb3VibGUgKERPIE5PVCBVU0UpIDx0
ZXN0QGV4YW1wbGUubmV0Poi+BBMBAgAoBQJL46hJAhsDBQkACTqABgsJCAcDAgYV
CAIJCgsEFgIDAQIeAQIXgAAKCRA0t9ELwQjoOgLpA/9/si2QYmietY9a6VlAmMri
mhZeqo6zyn8zrO9RGU7+8jmeb5nVnXw1YmZcw2fiJgI9+tTMkTfomyR6k0EDvcEu
2Mg3USkVnJfrrkPjSL9EajW6VpOUNxlox3ZT1oyEo3OOnVF1gC1reWYfy7Ns9zIB
1leLXbMr86zYdCoXp0Xu4g==
=xsEd
-----END PGP PUBLIC KEY BLOCK-----
"""


class Query:
    def __init__(self, value):
        self._value = value
        self.query = mock.MagicMock()

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
        self.flush = mock.AsyncMock()

    def public_signing_key(self, **_kwargs):
        return Query(self._key)

    def committee(self, *, key: str, _public_signing_keys: bool = False):
        return Query(self._committees_after_commit[key])


@pytest.mark.asyncio
async def test_database_add_model_audits_inserted_key():
    data = MockData(None, committees_after_commit={})
    writer, _write, write_as = _make_foundation_committer_with_audit(data)
    insert_result = mock.MagicMock()
    insert_result.one_or_none.return_value = object()
    data.execute.return_value = insert_result
    key_model = keys_writer.sql.PublicSigningKey(
        fingerprint="fp1",
        algorithm=1,
        length=4096,
        created=datetime.datetime.now(datetime.UTC),
        latest_self_signature=None,
        expires=None,
        primary_declared_uid="Alice <alice@example.org>",
        secondary_declared_uids=[],
        apache_uid="alice",
        ascii_armored_key="-----BEGIN PGP PUBLIC KEY BLOCK-----\nbody\n-----END PGP PUBLIC KEY BLOCK-----\n",
    )
    key = SimpleNamespace(key_model=key_model)

    result = await writer._FoundationCommitter__database_add_model(key)

    assert isinstance(result, outcome.Result)
    data.begin_immediate.assert_awaited_once()
    data.commit.assert_awaited_once()
    write_as.append_to_audit_log.assert_called_once()
    audit_kwargs = write_as.append_to_audit_log.call_args.kwargs
    assert audit_kwargs["action"] == "key_insert"
    assert audit_kwargs["asf_uid"] == "alice"
    assert audit_kwargs["fingerprint"] == "fp1"
    assert audit_kwargs["key_apache_uid"] == "alice"


@pytest.mark.asyncio
async def test_delete_committee_keys_audits_committed_delete_when_sync_fails():
    key_orphaned = SimpleNamespace(fingerprint="fp1", committees=[])
    initial_committee = SimpleNamespace(public_signing_keys=[key_orphaned])
    data = MockData(None, committees_after_commit={"alpha": initial_committee})
    writer, write_as = _make_foundation_admin(data, "alpha")
    error_message = "Failed to remove KEYS file for committee alpha: boom"

    with mock.patch.object(
        writer,
        "_sync_committee_keys_file",
        new=mock.AsyncMock(side_effect=storage.AccessError(error_message)),
    ):
        with pytest.raises(storage.AccessError, match="boom"):
            await writer.delete_committee_keys()

    data.commit.assert_awaited_once()
    assert write_as.append_to_audit_log.call_count == 2

    delete_audit = write_as.append_to_audit_log.call_args_list[0].kwargs
    sync_failure_audit = write_as.append_to_audit_log.call_args_list[1].kwargs

    assert delete_audit["committee_key"] == "alpha"
    assert delete_audit["keys_unlinked"] == 1
    assert delete_audit["keys_deleted"] == 1
    assert delete_audit["fingerprints"] == ["fp1"]

    assert sync_failure_audit["action"] == "delete_committee_keys_sync_failed"
    assert sync_failure_audit["committee_key"] == "alpha"
    assert sync_failure_audit["error"] == error_message


@pytest.mark.asyncio
async def test_delete_committee_keys_removes_links_and_orphaned_keys():
    key_orphaned = SimpleNamespace(fingerprint="fp1", committees=[])
    key_shared = SimpleNamespace(fingerprint="fp2", committees=[SimpleNamespace(key="beta")])
    initial_committee = SimpleNamespace(public_signing_keys=[key_orphaned, key_shared])
    data = MockData(None, committees_after_commit={"alpha": initial_committee})
    writer, write_as = _make_foundation_admin(data, "alpha")

    with mock.patch.object(writer, "_sync_committee_keys_file", new_callable=mock.AsyncMock) as mock_sync:
        num_unlinked, num_deleted = await writer.delete_committee_keys()

    assert num_unlinked == 2
    assert num_deleted == 1
    data.delete.assert_awaited_once_with(key_orphaned)
    data.flush.assert_awaited_once()
    data.commit.assert_awaited_once()
    mock_sync.assert_awaited_once_with("alpha")
    write_as.append_to_audit_log.assert_called_once()
    audit_kwargs = write_as.append_to_audit_log.call_args[1]
    assert audit_kwargs["asf_uid"] == "alice"
    assert audit_kwargs["committee_key"] == "alpha"
    assert audit_kwargs["keys_unlinked"] == 2
    assert audit_kwargs["keys_deleted"] == 1
    assert set(audit_kwargs["fingerprints"]) == {"fp1", "fp2"}


@pytest.mark.asyncio
async def test_delete_committee_keys_returns_zero_when_no_keys():
    empty_committee = SimpleNamespace(public_signing_keys=[])
    data = MockData(None, committees_after_commit={"alpha": empty_committee})
    writer, write_as = _make_foundation_admin(data, "alpha")

    num_unlinked, num_deleted = await writer.delete_committee_keys()

    assert num_unlinked == 0
    assert num_deleted == 0
    data.delete.assert_not_awaited()
    data.commit.assert_not_awaited()
    write_as.append_to_audit_log.assert_not_called()


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
    writer, _write, write_as = _make_foundation_committer_with_audit(data)

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
    write_as.append_to_audit_log.assert_called_once()
    audit_kwargs = write_as.append_to_audit_log.call_args.kwargs
    assert audit_kwargs["action"] == "key_delete"
    assert audit_kwargs["fingerprint"] == "fp1"
    assert audit_kwargs["committee_keys"] == ["alpha"]


@pytest.mark.asyncio
async def test_ensure_reuses_supplied_ldap_data_for_bulk_import() -> None:
    data = MockData(None, committees_after_commit={})
    writer, _write_as = _make_foundation_admin(data, "alpha")
    database_outcomes = outcome.List()
    ldap_data = {"alice@example.org": "alice"}

    with (
        mock.patch.object(keys_writer.util, "email_to_uid_map", new=mock.AsyncMock()) as email_to_uid_map,
        mock.patch.object(keys_writer.util, "parse_key_blocks", return_value=["block-one", "block-two"]),
        mock.patch.object(
            writer,
            "_CommitteeParticipant__block_models",
            side_effect=[
                [SimpleNamespace(fingerprint="one")],
                [SimpleNamespace(fingerprint="two")],
            ],
        ) as block_models,
        mock.patch.object(
            writer,
            "_CommitteeParticipant__database_add_models",
            new=mock.AsyncMock(return_value=database_outcomes),
        ) as database_add_models,
    ):
        result = await writer._CommitteeParticipant__ensure("keys text", ldap_data=ldap_data)

    assert result is database_outcomes
    email_to_uid_map.assert_not_awaited()
    assert block_models.call_count == 2
    assert {call.args[0] for call in block_models.call_args_list} == {"block-one", "block-two"}
    assert all(call.args[1] is ldap_data for call in block_models.call_args_list)
    database_add_models.assert_awaited_once()
    parsed_outcomes = database_add_models.await_args.args[0]
    assert parsed_outcomes.result_count == 2


def test_key_expires_at_uses_v4_user_binding_expiration() -> None:
    key, _ = keys_writer.openpgp.PublicKey.from_armor(_EMBEDDED_V4_EXPIRING_KEY_ASC)
    binding = next(iter(key.user_bindings()))
    binding_signature = binding.signatures[0]

    expires = keys_writer._key_expires_at(key)

    assert expires == datetime.datetime.fromtimestamp(
        key.created_at + binding_signature.key_expiration_seconds,
        datetime.UTC,
    )


def test_key_length_returns_dsa_bits() -> None:
    key = SimpleNamespace(
        public_key_algorithm="dsa",
        public_params=SimpleNamespace(rsa_bits=None, dsa_bits=3072, curve_bits=None),
    )

    length = keys_writer._key_length(key)

    assert length == 3072


def test_public_key_model_stores_latest_self_signature_separately_from_expiry() -> None:
    key, _ = keys_writer.openpgp.PublicKey.from_armor(_EMBEDDED_V4_EXPIRING_KEY_ASC)
    binding = next(iter(key.user_bindings()))
    binding_signature = binding.signatures[0]
    data = MockData(None, committees_after_commit={})
    writer, _write, _write_as = _make_foundation_committer_with_audit(data)

    key_model = writer.public_key_model(key, {}, original_key_block=_EMBEDDED_V4_EXPIRING_KEY_ASC)
    latest_self_signature = keys_writer._latest_self_signature(key)

    assert latest_self_signature is not None
    assert key_model.latest_self_signature == datetime.datetime.fromtimestamp(
        latest_self_signature.creation_time, datetime.UTC
    )
    assert key_model.expires == datetime.datetime.fromtimestamp(
        key.created_at + binding_signature.key_expiration_seconds, datetime.UTC
    )
    assert key_model.latest_self_signature != key_model.expires


@pytest.mark.asyncio
async def test_update_committee_associations_removal_deletes_empty_keys_file(tmp_path):
    owned_key = SimpleNamespace(fingerprint="fp1", committees=[SimpleNamespace(key="alpha")])
    data = MockData(
        owned_key,
        committees_after_commit={"alpha": _committee("alpha", [])},
    )
    writer, write, write_as = _make_foundation_committer_with_audit(data)

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
    write_as.append_to_audit_log.assert_called_once()
    audit_kwargs = write_as.append_to_audit_log.call_args.kwargs
    assert audit_kwargs["action"] == "key_update_committee_associations"
    assert audit_kwargs["fingerprint"] == "fp1"
    assert audit_kwargs["committees_added"] == []
    assert audit_kwargs["committees_removed"] == ["alpha"]


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


def _make_foundation_admin(data: MockData, committee_key: str):
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    write_as = mock.MagicMock()
    return keys_writer.FoundationAdmin(write, write_as, data, committee_key), write_as


def _make_foundation_committer(data: MockData):
    writer, write, _write_as = _make_foundation_committer_with_audit(data)
    return writer, write


def _make_foundation_committer_with_audit(data: MockData):
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    write.as_committee_participant = mock.MagicMock()
    write_as = mock.MagicMock()
    return keys_writer.FoundationCommitter(write, write_as, data), write, write_as


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
