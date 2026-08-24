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
import pathlib
import unittest.mock as mock
from types import SimpleNamespace

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.pgp as pgp
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.storage.outcome as outcome
import atr.storage.writers.keys as keys_writer
import tests.unit.pgp_fixtures as pgp_fixtures

_ALPHA_BLOCK = pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC
_ALPHA_FINGERPRINT = pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT
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


@pytest.fixture
async def sqlite_data():
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    async with sessionmaker() as data:
        yield data
    await engine.dispose()


class Query:
    def __init__(self, value):
        self._value = value
        self.query = mock.MagicMock()

    async def get(self):
        return self._value

    async def all(self):
        return [self._value]

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

    def signing_certificate(self, **_kwargs):
        return Query(self._key)

    def committee(self, *, key: str, _signing_certificates: bool = False):
        return Query(self._committees_after_commit[key])

    def release(self, *_args, **_kwargs):
        return Query(SimpleNamespace(project=mock.AsyncMock()))


def test_block_downgrade_reason_compares_the_named_certificate() -> None:
    fingerprint = pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT
    stored = pgp_fixtures.two_certificate_block(
        pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC
    )

    assert keys_writer._block_downgrade_reason(fingerprint, stored, pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC) is None


def test_block_models_splits_a_two_certificate_block() -> None:
    writer, _write_as = _make_committee_member(MockData(None, committees_after_commit={}), "test")
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    )

    keys = writer._CommitteeParticipant__block_models(block, keys_writer.cache.EmailUidLookup({}))

    fingerprints = [key.key_model.fingerprint for key in keys if isinstance(key, keys_writer.datatypes.Key)]
    assert fingerprints == [
        pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT,
        pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT,
    ]
    for key in keys:
        assert isinstance(key, keys_writer.datatypes.Key)
        own = pgp.certificate_for_fingerprint(key.key_model.ascii_armored_key, key.key_model.fingerprint)
        assert own is not None
        assert key.key_model.ascii_armored_key != block


def test_block_models_keeps_the_raw_bytes_of_a_single_certificate_block() -> None:
    writer, _write_as = _make_committee_member(MockData(None, committees_after_commit={}), "test")

    keys = writer._CommitteeParticipant__block_models(_ALPHA_BLOCK, keys_writer.cache.EmailUidLookup({}))

    assert len(keys) == 1
    assert isinstance(keys[0], keys_writer.datatypes.Key)
    assert keys[0].input == pgp._dearmored(_ALPHA_BLOCK)
    assert pgp.certificate_block_fingerprint(keys[0].key_model.ascii_armored_key) == _ALPHA_FINGERPRINT


def test_block_models_reports_a_failing_certificate_without_losing_its_siblings() -> None:
    writer, _write_as = _make_committee_member(MockData(None, committees_after_commit={}), "test")
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    )
    strengths = iter([None, ValueError("too weak")])

    def validate(_public_key) -> None:
        if (error := next(strengths)) is not None:
            raise error

    with mock.patch.object(keys_writer, "_validate_key_strength", side_effect=validate):
        keys = writer._CommitteeParticipant__block_models(block, keys_writer.cache.EmailUidLookup({}))

    assert isinstance(keys[0], keys_writer.datatypes.Key)
    assert keys[0].key_model.fingerprint == pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT
    assert isinstance(keys[1], ValueError)


@pytest.mark.asyncio
async def test_database_add_model_writes_a_genesis_row_and_audits_the_insert(sqlite_data):
    writer, _write, write_as = _make_foundation_committer_with_audit(sqlite_data)
    key = _parsed_key(_ALPHA_BLOCK)
    key.key_model.apache_uid = "alice"

    result, publications = await writer._FoundationCommitter__database_add_model(key, "web:req-1")

    assert result.result_or_raise().status == datatypes.KeyStatus.INSERTED
    assert publications == {}
    rows = await sqlite_data.key_attestable(fingerprint=_ALPHA_FINGERPRINT).all()
    assert [(row.seq, row.operation, row.source, row.actor, row.role) for row in rows] == [
        (1, sql.KeyOperation.REVISE, "web:req-1", "alice", sql.KeyRole.USER)
    ]
    assert (rows[0].deletions, rows[0].input) == (None, pgp._dearmored(_ALPHA_BLOCK))
    stored = await sqlite_data.signing_certificate(fingerprint=_ALPHA_FINGERPRINT).get()
    assert stored is not None
    assert pgp.fold_deltas([(None, rows[0].additions)]) == pgp.certificate_placements(stored.ascii_armored_key)
    audit_kwargs = write_as.append_to_audit_log.call_args.kwargs
    assert (audit_kwargs["action"], audit_kwargs["asf_uid"]) == ("key_insert", "alice")
    assert audit_kwargs["fingerprint"] == _ALPHA_FINGERPRINT


def test_deduplicate_keys_keeps_identical_copies_once() -> None:
    armored = pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    re_armored = keys_writer.openpgp.composed.SignedPublicKey.from_armor(armored)[0].to_armored()

    deduplicated = keys_writer._deduplicate_keys([_parsed_key(armored), _parsed_key(re_armored)])

    assert len(deduplicated) == 1
    assert isinstance(deduplicated[0], keys_writer.datatypes.Key)


def test_deduplicate_keys_merges_the_copies_of_one_certificate() -> None:
    full = pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    stripped = pgp_fixtures.block_without_signature_type(full, 0x28)
    other = _parsed_key(_ALPHA_BLOCK)

    deduplicated = keys_writer._deduplicate_keys([_parsed_key(stripped), other, _parsed_key(full)])

    assert len(deduplicated) == 2
    assert isinstance(deduplicated[0], keys_writer.datatypes.Key)
    assert deduplicated[0].key_model.ascii_armored_key == pgp.merge_certificate_blocks([full])
    assert deduplicated[0].input == pgp._dearmored(stripped) + pgp._dearmored(full)
    assert deduplicated[1] is other


@pytest.mark.asyncio
async def test_database_add_models_links_a_stored_key_whose_reimport_is_a_subset(sqlite_data):
    full = pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    fingerprint = pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT
    sqlite_data.add(keys_writer.sql.Committee(key="alpha"))
    sqlite_data.add(_signing_certificate(fingerprint, apache_uid="alice", armored=full))
    sqlite_data.add(_genesis_row(fingerprint, full, "alice"))
    await sqlite_data.commit()
    writer, _write_as = _make_committee_member(sqlite_data, "alpha")
    outcomes = outcome.List[keys_writer.datatypes.Key]()
    outcomes.append_result(_parsed_key(pgp_fixtures.block_without_signature_type(full, 0x28)))

    with mock.patch.object(writer, "_recheck_committee_drafts", new_callable=mock.AsyncMock):
        outcomes, _publications = await writer._CommitteeParticipant__database_add_models(outcomes, "web:req-1")

    assert outcomes.error_count == 0
    assert outcomes.results()[0].status == datatypes.KeyStatus.LINKED
    kept = await sqlite_data.signing_certificate(fingerprint=fingerprint).get()
    assert (kept is not None) and (kept.ascii_armored_key == full)
    rows = await sqlite_data.key_attestable(fingerprint=fingerprint).all()
    assert [(row.seq, row.source, row.role) for row in rows] == [(1, "web:seed", sql.KeyRole.USER)]
    links = (await sqlite_data.execute(sqlmodel.select(keys_writer.sql.KeyLink))).all()
    assert [(link[0].committee_key, link[0].key_fingerprint) for link in links] == [("alpha", fingerprint)]


@pytest.mark.asyncio
async def test_database_add_models_stores_the_other_keys_when_one_fails(sqlite_data):
    sqlite_data.add(keys_writer.sql.Committee(key="alpha"))
    sqlite_data.add(_signing_certificate(_ALPHA_FINGERPRINT, apache_uid="alice", armored=_ALPHA_BLOCK))
    await sqlite_data.commit()
    writer, write_as = _make_committee_member(sqlite_data, "alpha")
    outcomes = outcome.List[keys_writer.datatypes.Key]()
    outcomes.append_result(_parsed_key(_ALPHA_BLOCK))
    outcomes.append_result(_parsed_key(pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC))

    with mock.patch.object(writer, "_recheck_committee_drafts", new_callable=mock.AsyncMock):
        outcomes, _publications = await writer._CommitteeParticipant__database_add_models(outcomes, "web:req-1")

    errors = [str(error) for error in outcomes.errors()]
    assert (outcomes.result_count, outcomes.error_count) == (1, 1)
    assert "has no log" in errors[0]
    assert outcomes.results()[0].status == datatypes.KeyStatus.INSERTED | datatypes.KeyStatus.LINKED
    assert await sqlite_data.key_attestable(fingerprint=_ALPHA_FINGERPRINT).all() == []
    rows = await sqlite_data.key_attestable(fingerprint=pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT).all()
    assert [(row.seq, row.source) for row in rows] == [(1, "web:req-1")]
    links = (await sqlite_data.execute(sqlmodel.select(keys_writer.sql.KeyLink))).all()
    assert [(link[0].committee_key, link[0].key_fingerprint) for link in links] == [
        ("alpha", pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT)
    ]
    write_as.append_to_audit_log.assert_called_once()
    audit_kwargs = write_as.append_to_audit_log.call_args.kwargs
    assert (audit_kwargs["action"], audit_kwargs["committee_key"], audit_kwargs["linked"]) == (
        "key_insert",
        "alpha",
        True,
    )
    assert audit_kwargs["fingerprint"] == pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT


@pytest.mark.asyncio
async def test_delete_committee_keys_audits_committed_delete_when_sync_fails(sqlite_data):
    await _seed_committee_key(sqlite_data, "alpha", _ALPHA_FINGERPRINT, _ALPHA_BLOCK)
    writer, write_as = _make_foundation_admin(sqlite_data, "alpha")
    error_message = "Failed to remove KEYS file for committee alpha: permission denied"

    with mock.patch.object(
        writer,
        "_sync_committee_keys_file",
        new=mock.AsyncMock(side_effect=storage.AccessError(error_message)),
    ):
        with pytest.raises(storage.AccessError, match="permission denied"):
            await writer.delete_committee_keys("web:req-1")

    assert write_as.append_to_audit_log.call_count == 2

    delete_audit = write_as.append_to_audit_log.call_args_list[0].kwargs
    sync_failure_audit = write_as.append_to_audit_log.call_args_list[1].kwargs

    assert delete_audit["committee_key"] == "alpha"
    assert delete_audit["keys_unlinked"] == 1
    assert delete_audit["keys_deleted"] == 1
    assert delete_audit["fingerprints"] == [_ALPHA_FINGERPRINT]

    assert sync_failure_audit["action"] == "delete_committee_keys_sync_failed"
    assert sync_failure_audit["committee_key"] == "alpha"
    assert sync_failure_audit["error"] == error_message

    key = await sqlite_data.signing_certificate(fingerprint=_ALPHA_FINGERPRINT, deleted=True).get()
    assert key is not None


@pytest.mark.asyncio
async def test_delete_committee_keys_removes_links_and_orphaned_keys(sqlite_data):
    await _seed_committee_key(sqlite_data, "alpha", _ALPHA_FINGERPRINT, _ALPHA_BLOCK)
    sqlite_data.add(keys_writer.sql.Committee(key="beta"))
    sqlite_data.add(_signing_certificate("fp2", apache_uid="bob"))
    await sqlite_data.commit()
    sqlite_data.add(keys_writer.sql.KeyLink(committee_key="alpha", key_fingerprint="fp2"))
    sqlite_data.add(keys_writer.sql.KeyLink(committee_key="beta", key_fingerprint="fp2"))
    await sqlite_data.commit()
    writer, write_as = _make_foundation_admin(sqlite_data, "alpha")

    with mock.patch.object(writer, "_sync_committee_keys_file", new_callable=mock.AsyncMock) as mock_sync:
        mock_sync.return_value = (0, outcome.Result(keys_writer.datatypes.KeysPublish.SVN_NOT_CONFIGURED))
        num_unlinked, num_deleted, _ = await writer.delete_committee_keys("web:req-1")

    assert num_unlinked == 2
    assert num_deleted == 1
    orphaned = await sqlite_data.signing_certificate(fingerprint=_ALPHA_FINGERPRINT, deleted=True).get()
    assert orphaned is not None
    rows = await sqlite_data.key_attestable(fingerprint=_ALPHA_FINGERPRINT).all()
    assert [(row.seq, row.operation, row.source, row.role) for row in rows] == [
        (1, sql.KeyOperation.REVISE, "web:seed", sql.KeyRole.USER),
        (2, sql.KeyOperation.DELETE, "web:req-1", sql.KeyRole.USER),
    ]
    assert (rows[1].deletions, rows[1].additions) == (None, None)
    shared = await sqlite_data.signing_certificate(fingerprint="fp2").get()
    assert shared is not None
    assert shared.deleted is None
    links = (await sqlite_data.execute(sqlmodel.select(keys_writer.sql.KeyLink))).all()
    assert [(link[0].committee_key, link[0].key_fingerprint) for link in links] == [("beta", "fp2")]
    mock_sync.assert_awaited_once_with("alpha")
    write_as.append_to_audit_log.assert_called_once()
    audit_kwargs = write_as.append_to_audit_log.call_args[1]
    assert audit_kwargs["asf_uid"] == "alice"
    assert audit_kwargs["committee_key"] == "alpha"
    assert audit_kwargs["keys_unlinked"] == 2
    assert audit_kwargs["keys_deleted"] == 1
    assert set(audit_kwargs["fingerprints"]) == {_ALPHA_FINGERPRINT, "fp2"}


@pytest.mark.asyncio
async def test_delete_committee_keys_returns_zero_when_no_keys(sqlite_data):
    sqlite_data.add(keys_writer.sql.Committee(key="alpha"))
    await sqlite_data.commit()
    writer, write_as = _make_foundation_admin(sqlite_data, "alpha")

    num_unlinked, num_deleted, _ = await writer.delete_committee_keys("web:req-1")

    assert num_unlinked == 0
    assert num_deleted == 0
    write_as.append_to_audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_dissociate_fingerprints_removes_links_and_keeps_certificates(sqlite_data):
    sqlite_data.add(keys_writer.sql.Committee(key="alpha"))
    sqlite_data.add(_signing_certificate("fp1", apache_uid="alice"))
    await sqlite_data.commit()
    sqlite_data.add(keys_writer.sql.KeyLink(committee_key="alpha", key_fingerprint="fp1"))
    sqlite_data.add(keys_writer.sql.Committee(key="beta"))
    sqlite_data.add(_signing_certificate("fp2", apache_uid="bob"))
    await sqlite_data.commit()
    sqlite_data.add(keys_writer.sql.KeyLink(committee_key="alpha", key_fingerprint="fp2"))
    sqlite_data.add(keys_writer.sql.KeyLink(committee_key="beta", key_fingerprint="fp2"))
    await sqlite_data.commit()
    writer, write_as = _make_foundation_admin(sqlite_data, "alpha")

    with mock.patch.object(writer, "_sync_committee_keys_file", new_callable=mock.AsyncMock) as mock_sync:
        mock_sync.return_value = (0, outcome.Result(keys_writer.datatypes.KeysPublish.AUTOMATION_DISABLED))
        removed, publication = await writer.dissociate_fingerprints(["fp1", "fp2", "fp9"])

    assert removed == ["fp1", "fp2"]
    assert publication is not None
    assert publication.result_or_none() is keys_writer.datatypes.KeysPublish.AUTOMATION_DISABLED
    for fingerprint in ("fp1", "fp2"):
        certificate = await sqlite_data.signing_certificate(fingerprint=fingerprint).get()
        assert certificate is not None
        assert certificate.deleted is None
    links = (await sqlite_data.execute(sqlmodel.select(keys_writer.sql.KeyLink))).all()
    assert [(link[0].committee_key, link[0].key_fingerprint) for link in links] == [("beta", "fp2")]
    mock_sync.assert_awaited_once_with("alpha")
    write_as.append_to_audit_log.assert_called_once()
    audit_kwargs = write_as.append_to_audit_log.call_args[1]
    assert audit_kwargs["action"] == "key_dissociate_committee"
    assert audit_kwargs["committee_key"] == "alpha"
    assert audit_kwargs["fingerprints"] == ["fp1", "fp2"]


@pytest.mark.asyncio
async def test_delete_key_removal_publishes_empty_keys_file(sqlite_data):
    await _seed_committee_key(sqlite_data, "alpha", _ALPHA_FINGERPRINT, _ALPHA_BLOCK)
    writer, _write, write_as = _make_foundation_committer_with_audit(sqlite_data)

    publish = mock.AsyncMock(return_value=outcome.Result(datatypes.KeysPublish.PUBLISHED))
    with mock.patch.object(writer, "_publish_keys_to_svn", publish):
        result = await writer.delete_key(_ALPHA_FINGERPRINT, "web:req-1")

    assert isinstance(result, outcome.Result)
    publish.assert_awaited_once()
    assert publish.await_args.args[1] is None
    write_as.append_to_audit_log.assert_called_once()
    audit_kwargs = write_as.append_to_audit_log.call_args.kwargs
    assert audit_kwargs["action"] == "key_delete"
    assert audit_kwargs["fingerprint"] == _ALPHA_FINGERPRINT
    assert audit_kwargs["committee_keys"] == ["alpha"]
    rows = await sqlite_data.key_attestable(fingerprint=_ALPHA_FINGERPRINT).all()
    assert [(row.seq, row.operation) for row in rows] == [(1, sql.KeyOperation.REVISE), (2, sql.KeyOperation.DELETE)]


@pytest.mark.asyncio
async def test_ensure_allows_key_without_apache_uid_for_bulk_import() -> None:
    data = MockData(None, committees_after_commit={})
    writer, _write_as = _make_foundation_admin(data, "alpha")
    key = keys_writer.datatypes.Key(
        status=keys_writer.datatypes.KeyStatus.PARSED,
        key_model=_signing_certificate("fp1", apache_uid=None),
    )
    database_outcomes = outcome.List(outcome.Result(key))
    lookup = keys_writer.cache.EmailUidLookup({})

    with (
        mock.patch.object(
            keys_writer.cache, "email_uid_view_or_live", new=mock.AsyncMock(return_value=lookup)
        ) as email_uid_view,
        mock.patch.object(keys_writer.util, "parse_key_blocks", return_value=["block-one"]),
        mock.patch.object(writer, "_CommitteeParticipant__block_models", return_value=[key]) as block_models,
        mock.patch.object(
            writer,
            "_CommitteeParticipant__database_add_models",
            new=mock.AsyncMock(return_value=(database_outcomes, {})),
        ) as database_add_models,
    ):
        result, publications = await writer._CommitteeParticipant__ensure("keys text", "web:req-1")

    assert result is database_outcomes
    assert publications == {}
    email_uid_view.assert_awaited_once()
    block_models.assert_called_once_with("block-one", lookup)
    database_add_models.assert_awaited_once()
    parsed_outcomes = database_add_models.await_args.args[0]
    assert parsed_outcomes.result_count == 1


@pytest.mark.asyncio
async def test_ensure_stored_one_accepts_key_with_apache_uid() -> None:
    data = MockData(None, committees_after_commit={})
    writer, _write, _write_as = _make_foundation_committer_with_audit(data)
    key = keys_writer.datatypes.Key(
        status=keys_writer.datatypes.KeyStatus.PARSED,
        key_model=_signing_certificate("fp1", apache_uid="alice"),
    )
    database_outcome = outcome.Result(key)

    with (
        mock.patch.object(keys_writer.cache, "email_uid_view_or_live", new=mock.AsyncMock()) as email_uid_view,
        mock.patch.object(keys_writer.util, "parse_key_blocks", return_value=["block-one"]),
        mock.patch.object(writer, "_FoundationCommitter__block_model_create", return_value=key) as block_model_create,
        mock.patch.object(writer, "_FoundationCommitter__block_model") as block_model,
        mock.patch.object(
            writer,
            "_FoundationCommitter__database_add_model",
            new=mock.AsyncMock(return_value=(database_outcome, {})),
        ) as database_add_model,
    ):
        result, publications = await writer.ensure_stored_one("keys text", "web:req-1")

    assert result is database_outcome
    assert publications == {}
    email_uid_view.assert_not_awaited()
    block_model_create.assert_called_once()
    assert block_model_create.call_args.args[0] == "block-one"
    assert isinstance(block_model_create.call_args.args[1], keys_writer.cache.EmailUidLookup)
    block_model.assert_not_called()
    database_add_model.assert_awaited_once_with(key, "web:req-1")


@pytest.mark.asyncio
async def test_ensure_stored_one_accepts_test_key_without_email_cache() -> None:
    data = MockData(None, committees_after_commit={})
    writer, _write, _write_as = _make_foundation_committer_with_audit(data, asf_uid="test")
    key_text = _playwright_test_key_text()

    with (
        mock.patch.object(keys_writer.config, "is_test_mode", return_value=True),
        mock.patch.object(
            keys_writer.cache,
            "email_uid_view_or_live",
            new=mock.AsyncMock(side_effect=AssertionError("cache should not be needed for the test key")),
        ) as email_uid_view,
        mock.patch.object(
            writer,
            "_FoundationCommitter__database_add_model",
            new=mock.AsyncMock(side_effect=lambda key, _source: (outcome.Result(key), {})),
        ) as database_add_model,
    ):
        result, _publications = await writer.ensure_stored_one(key_text, "web:req-1")

    key = result.result_or_raise()
    assert key.key_model.apache_uid == "test"
    assert key.key_model.fingerprint == "557f8d855def8bbe2dc5603b64c271bb87b7fe7b"
    email_uid_view.assert_not_awaited()
    database_add_model.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_stored_one_rejects_a_block_with_two_keys() -> None:
    data = MockData(None, committees_after_commit={})
    writer, _write, _write_as = _make_foundation_committer_with_audit(data)
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    )

    result, publications = await writer.ensure_stored_one(block, "web:req-1")

    assert publications == {}
    assert "contains 2 keys" in str(result.error_or_none())


@pytest.mark.asyncio
async def test_ensure_stored_one_rejects_key_without_apache_uid() -> None:
    data = MockData(None, committees_after_commit={})
    writer, _write, _write_as = _make_foundation_committer_with_audit(data)
    key = keys_writer.datatypes.Key(
        status=keys_writer.datatypes.KeyStatus.PARSED,
        key_model=_signing_certificate("fp1", apache_uid=None),
    )
    lookup = keys_writer.cache.EmailUidLookup({})

    with (
        mock.patch.object(
            keys_writer.cache, "email_uid_view_or_live", new=mock.AsyncMock(return_value=lookup)
        ) as email_uid_view,
        mock.patch.object(keys_writer.util, "parse_key_blocks", return_value=["block-one"]),
        mock.patch.object(
            writer, "_FoundationCommitter__block_model_create", side_effect=[key, key]
        ) as block_model_create,
        mock.patch.object(
            writer,
            "_FoundationCommitter__database_add_model",
            new=mock.AsyncMock(),
        ) as database_add_model,
    ):
        result, publications = await writer.ensure_stored_one("keys text", "web:req-1")

    assert isinstance(result, outcome.Error)
    assert publications == {}
    error = result.error_or_none()
    assert isinstance(error, keys_writer.datatypes.UnknownApacheUidError)
    assert str(error) == "OpenPGP key could not be associated with an ASF UID. Import it through a KEYS file instead."
    email_uid_view.assert_awaited_once()
    assert block_model_create.call_count == 2
    assert all(call.args[0] == "block-one" for call in block_model_create.call_args_list)
    assert isinstance(block_model_create.call_args_list[0].args[1], keys_writer.cache.EmailUidLookup)
    assert block_model_create.call_args_list[1].args[1] is lookup
    database_add_model.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_uses_cached_email_lookup_for_bulk_import() -> None:
    data = MockData(None, committees_after_commit={})
    writer, _write_as = _make_foundation_admin(data, "alpha")
    database_outcomes = outcome.List()
    lookup = keys_writer.cache.EmailUidLookup({"deadbeef": "alice"})

    with (
        mock.patch.object(
            keys_writer.cache, "email_uid_view_or_live", new=mock.AsyncMock(return_value=lookup)
        ) as email_uid_view,
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
            new=mock.AsyncMock(return_value=(database_outcomes, {})),
        ) as database_add_models,
    ):
        result, _publications = await writer._CommitteeParticipant__ensure("keys text", "web:req-1")

    assert result is database_outcomes
    email_uid_view.assert_awaited_once()
    assert block_models.call_count == 2
    assert {call.args[0] for call in block_models.call_args_list} == {"block-one", "block-two"}
    assert all(call.args[1] is lookup for call in block_models.call_args_list)
    database_add_models.assert_awaited_once()
    parsed_outcomes = database_add_models.await_args.args[0]
    assert parsed_outcomes.result_count == 2


def test_key_expires_at_uses_v4_user_binding_expiration() -> None:
    key, _ = keys_writer.openpgp.composed.SignedPublicKey.from_armor(_EMBEDDED_V4_EXPIRING_KEY_ASC)
    user = next(iter(key.details.users))
    binding_signature = user.signatures[0]

    expires = pgp.key_expires_at(key)

    assert expires == datetime.datetime.fromtimestamp(
        key.created_at + binding_signature.key_expiration_time(),
        datetime.UTC,
    )


def test_key_length_returns_dsa_bits() -> None:
    key = SimpleNamespace(
        public_key_algorithm="dsa",
        public_params=SimpleNamespace(rsa_bits=None, dsa_bits=3072, curve_bits=None),
    )

    length = keys_writer._key_length(key)

    assert length == 3072


def test_certificate_records_its_latest_self_signature() -> None:
    key, _ = keys_writer.openpgp.composed.SignedPublicKey.from_armor(_EMBEDDED_V4_EXPIRING_KEY_ASC)
    data = MockData(None, committees_after_commit={})
    writer, _write, _write_as = _make_foundation_committer_with_audit(data)

    key_model = writer.public_key_model(
        key, keys_writer.cache.EmailUidLookup({}), original_key_block=_EMBEDDED_V4_EXPIRING_KEY_ASC
    )
    latest_self_signature = pgp.latest_self_signature(key)

    assert latest_self_signature is not None
    assert key_model.latest_self_signature == datetime.datetime.fromtimestamp(
        latest_self_signature.created(), datetime.UTC
    )


def test_expiry_is_recorded_against_the_signing_key_not_the_certificate() -> None:
    key, _ = keys_writer.openpgp.composed.SignedPublicKey.from_armor(_EMBEDDED_V4_EXPIRING_KEY_ASC)
    user = next(iter(key.details.users))
    binding_signature = user.signatures[0]

    # Expiry is per key, so it belongs to the SigningKey rows and not to the certificate above them
    assert not hasattr(sql.SigningCertificate, "expires")
    primary = next(facts for facts in pgp.signing_key_facts(key) if facts.is_primary)

    assert primary.expires == datetime.datetime.fromtimestamp(
        key.created_at + binding_signature.key_expiration_time(), datetime.UTC
    )


@pytest.mark.asyncio
async def test_publish_keys_to_svn_distinguishes_publication_states() -> None:
    writer, _write, _write_as = _make_foundation_committer_with_audit(MockData(None, committees_after_commit={}))
    committee = _committee("alpha", [])

    with mock.patch.object(keys_writer.config, "get", return_value=SimpleNamespace(SVN_PUBLISH_URL=None)):
        unconfigured = await writer._publish_keys_to_svn(committee, None)
    with (
        mock.patch.object(
            keys_writer.config, "get", return_value=SimpleNamespace(SVN_PUBLISH_URL="https://svn.example/dist/dev/atr")
        ),
        mock.patch.object(
            keys_writer.util, "svn_publish_internal_url", return_value="https://svn.example/dist/dev/atr/alpha"
        ),
        mock.patch.object(keys_writer.svn, "publish_file", new_callable=mock.AsyncMock),
    ):
        published = await writer._publish_keys_to_svn(committee, None)

    assert unconfigured.result_or_raise() is keys_writer.datatypes.KeysPublish.SVN_NOT_CONFIGURED
    assert published.result_or_raise() is keys_writer.datatypes.KeysPublish.PUBLISHED


@pytest.mark.asyncio
async def test_publish_keys_to_svn_puts_keys_url() -> None:
    writer, _write, _write_as = _make_foundation_committer_with_audit(MockData(None, committees_after_commit={}))
    committee = _committee("alpha", [])
    with (
        mock.patch.object(
            keys_writer.config, "get", return_value=SimpleNamespace(SVN_PUBLISH_URL="https://svn.example/dist/dev/atr")
        ),
        mock.patch.object(
            keys_writer.util, "svn_publish_internal_url", return_value="https://svn.example/dist/dev/atr/alpha"
        ),
        mock.patch.object(keys_writer.svn, "publish_file", new_callable=mock.AsyncMock) as publish_file,
    ):
        result = await writer._publish_keys_to_svn(committee, None)

    publish_file.assert_awaited_once()
    assert publish_file.call_args.args[1] == "https://svn.example/dist/dev/atr/alpha/KEYS"
    assert result.ok


@pytest.mark.asyncio
async def test_publish_keys_to_svn_skips_when_automation_disabled() -> None:
    writer, _write, _write_as = _make_foundation_committer_with_audit(MockData(None, committees_after_commit={}))
    committee = _committee("alpha", [], automated_keys_file=False)
    with (
        mock.patch.object(
            keys_writer.config, "get", return_value=SimpleNamespace(SVN_PUBLISH_URL="https://svn.example/dist/dev/atr")
        ),
        mock.patch.object(keys_writer.svn, "publish_file", new_callable=mock.AsyncMock) as publish_file,
    ):
        result = await writer._publish_keys_to_svn(committee, None)

    publish_file.assert_not_awaited()
    assert result.result_or_raise() is keys_writer.datatypes.KeysPublish.AUTOMATION_DISABLED


@pytest.mark.asyncio
async def test_set_automated_keys_file_persists_and_audits(sqlite_data):
    sqlite_data.add(keys_writer.sql.Committee(key="alpha"))
    await sqlite_data.commit()
    writer, write_as = _make_committee_member(sqlite_data, "alpha")

    changed = await writer.set_automated_keys_file(False)
    unchanged = await writer.set_automated_keys_file(False)

    committee = await sqlite_data.committee(key="alpha").get()
    assert (changed, unchanged) == (True, False)
    assert (committee is not None) and (committee.automated_keys_file is False)
    write_as.append_to_audit_log.assert_called_once()


def test_set_automated_keys_file_requires_membership() -> None:
    authorisation = SimpleNamespace(asf_uid="alice", is_member_of=lambda _key: False)
    write = storage.Write(authorisation, mock.MagicMock())

    oc = write.as_committee_member_outcome("alpha")

    error = oc.error_or_none()
    assert isinstance(error, storage.AccessError)
    assert error.status == 403


def test_signing_key_rows_selects_the_certificate_matching_the_row() -> None:
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    )

    rows = keys_writer._signing_key_rows(pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT, block)

    assert rows is not None
    assert {row["fingerprint"] for row in rows if row["is_primary"]} == {
        pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT
    }
    assert keys_writer._signing_key_rows("0" * 40, block) is None


@pytest.mark.asyncio
async def test_sync_committees_for_keys_returns_publications():
    data = MockData(None, committees_after_commit={})
    writer, _write, _write_as = _make_foundation_committer_with_audit(data)
    link_rows = mock.MagicMock()
    link_rows.scalars.return_value.all.return_value = ["beta", "alpha"]
    data.execute.return_value = link_rows
    disabled = outcome.Result(keys_writer.datatypes.KeysPublish.AUTOMATION_DISABLED)
    published = outcome.Result(keys_writer.datatypes.KeysPublish.PUBLISHED)

    with (
        mock.patch.object(
            writer,
            "_sync_committee_keys_file",
            new=mock.AsyncMock(side_effect=[(None, disabled), ("alpha/KEYS", published)]),
        ) as sync_file,
        mock.patch.object(writer, "_recheck_committee_drafts", new=mock.AsyncMock()) as recheck,
    ):
        publications = await writer._FoundationCommitter__sync_committees_for_keys(["fp1"])

    assert publications == {"alpha": disabled, "beta": published}
    assert sync_file.await_args_list == [mock.call("alpha"), mock.call("beta")]
    recheck.assert_awaited_once_with("alpha", "beta")


@pytest.mark.asyncio
async def test_update_committee_associations_removal_publishes_empty_keys_file():
    owned_key = SimpleNamespace(fingerprint="fp1", committees=[SimpleNamespace(key="alpha")])
    data = MockData(
        owned_key,
        committees_after_commit={"alpha": _committee("alpha", [])},
    )
    writer, write, write_as = _make_foundation_committer_with_audit(data)

    publish = mock.AsyncMock(return_value=outcome.Result(datatypes.KeysPublish.PUBLISHED))
    with mock.patch.object(writer, "_publish_keys_to_svn", publish):
        update = await writer.update_committee_associations("fp1", [])

    assert update.added == set()
    assert update.removed == {"alpha"}
    publish.assert_awaited_once()
    assert publish.await_args.args[1] is None
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
async def test_update_committee_associations_removal_republishes_remaining_keys():
    owned_key = SimpleNamespace(fingerprint="fp1", committees=[SimpleNamespace(key="alpha")])
    remaining_key = _public_key("bbbbccccdddd1111")
    data = MockData(
        owned_key,
        committees_after_commit={"alpha": _committee("alpha", [remaining_key])},
    )
    writer, write = _make_foundation_committer(data)

    publish = mock.AsyncMock(return_value=outcome.Result(datatypes.KeysPublish.PUBLISHED))
    with mock.patch.object(writer, "_publish_keys_to_svn", publish):
        update = await writer.update_committee_associations("fp1", [])

    assert update.added == set()
    assert update.removed == {"alpha"}
    publish.assert_awaited_once()
    content = publish.await_args.args[1]
    assert remaining_key.fingerprint.upper() in content
    assert "Signing keys for the alpha committee" in content
    assert write.as_committee_participant.call_count == 0


def _committee(
    key: str, signing_certificates: list[object], *, is_podling: bool = False, automated_keys_file: bool = True
):
    return SimpleNamespace(
        key=key,
        is_podling=is_podling,
        signing_certificates=signing_certificates,
        automated_keys_file=automated_keys_file,
    )


def _make_committee_member(data, committee_key: str):
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    write_as = mock.MagicMock()
    return keys_writer.CommitteeMember(write, write_as, data, committee_key), write_as


def _make_foundation_admin(data: MockData, committee_key: str):
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    write_as = mock.MagicMock()
    return keys_writer.FoundationAdmin(write, write_as, data, committee_key), write_as


def _make_foundation_committer(data: MockData):
    writer, write, _write_as = _make_foundation_committer_with_audit(data)
    return writer, write


def _make_foundation_committer_with_audit(data: MockData, asf_uid: str = "alice"):
    write = mock.MagicMock()
    write.authorisation.asf_uid = asf_uid
    write.as_committee_participant = mock.MagicMock()
    write_as = mock.MagicMock()
    return keys_writer.FoundationCommitter(write, write_as, data), write, write_as


def _parsed_key(armored: str) -> keys_writer.datatypes.Key:
    key, _ = keys_writer.openpgp.composed.SignedPublicKey.from_armor(armored)
    model = _signing_certificate(key.fingerprint.lower(), apache_uid=None, armored=armored)
    return keys_writer.datatypes.Key(
        status=keys_writer.datatypes.KeyStatus.PARSED, key_model=model, input=pgp._dearmored(armored)
    )


def _playwright_test_key_text() -> str:
    key_path = (
        pathlib.Path(__file__).resolve().parents[2] / "playwright" / "557F8D855DEF8BBE2DC5603B64C271BB87B7FE7B.asc"
    )
    return key_path.read_text(encoding="utf-8")


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


def _genesis_row(fingerprint: str, armored: str, actor: str) -> sql.KeyAttestable:
    _, additions = pgp.delta_fragments(frozenset(), pgp.certificate_placements(armored))
    return sql.KeyAttestable(
        fingerprint=fingerprint,
        seq=1,
        operation=sql.KeyOperation.REVISE,
        source="web:seed",
        additions=additions,
        updated=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        actor=actor,
        role=sql.KeyRole.USER,
    )


async def _seed_committee_key(data: db.Session, committee_key: str, fingerprint: str, armored: str) -> None:
    data.add(keys_writer.sql.Committee(key=committee_key))
    data.add(_signing_certificate(fingerprint, apache_uid="alice", armored=armored))
    data.add(_genesis_row(fingerprint, armored, "alice"))
    await data.commit()
    data.add(keys_writer.sql.KeyLink(committee_key=committee_key, key_fingerprint=fingerprint))
    await data.commit()


def _signing_certificate(
    fingerprint: str, apache_uid: str | None, armored: str | None = None
) -> keys_writer.sql.SigningCertificate:
    return keys_writer.sql.SigningCertificate(
        fingerprint=fingerprint,
        latest_self_signature=None,
        primary_declared_uid="Alice <alice@example.org>",
        secondary_declared_uids=[],
        apache_uid=apache_uid,
        ascii_armored_key=armored or "-----BEGIN PGP PUBLIC KEY BLOCK-----\nbody\n-----END PGP PUBLIC KEY BLOCK-----\n",
    )
