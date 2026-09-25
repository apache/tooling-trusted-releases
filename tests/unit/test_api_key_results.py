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

import contextlib
import inspect
import unittest.mock as mock

import pytest
import sqlalchemy
import sqlmodel

import atr.api
import atr.cache as cache
import atr.models as models
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.pgp as pgp
import atr.storage.datatypes as datatypes
import atr.storage.outcome as outcome
import atr.storage.writers.keys as keys
import tests.unit.pgp_fixtures as pgp_fixtures


@pytest.mark.asyncio
async def test_key_add_reports_a_multi_key_block_as_a_bad_request() -> None:
    write = mock.MagicMock()
    write.as_foundation_committer.return_value.keys.ensure_stored_one = mock.AsyncMock(
        return_value=(outcome.Error(ValueError("This block contains 2 keys; add one key at a time")), {})
    )

    @contextlib.asynccontextmanager
    async def write_context(_asf_uid):
        yield write

    args = models.api.KeyAddArgs(asfuid="alice", key="-----BEGIN PGP PUBLIC KEY BLOCK-----\n", committees=[])
    with (
        mock.patch.object(atr.api, "_jwt_asf_uid", return_value="alice"),
        mock.patch.object(atr.api.storage, "write", new=write_context),
        pytest.raises(atr.api.exceptions.BadRequest, match="contains 2 keys"),
    ):
        await inspect.unwrap(atr.api.key_add)("key/add", args)


def test_key_delete_results_carry_publication_warnings() -> None:
    warning = (
        "KEYS publication to SVN was skipped for example because automated publication is disabled."
        " Remove the key manually if necessary."
    )
    results = models.api.KeyDeleteResults(endpoint="/key/delete", success=True, warnings=[warning])

    parsed = models.api.validate_key_delete(results.model_dump(mode="json"))

    assert parsed.warnings == [warning]
    assert models.api.KeyDeleteResults(endpoint="/key/delete", success=True).warnings == []


def test_key_response_copy_does_not_dirty_the_database() -> None:
    engine = sqlmodel.create_engine("sqlite://")
    sqlmodel.SQLModel.metadata.create_all(engine)
    with sqlmodel.Session(engine, expire_on_commit=False) as data:
        stored = _certificate()
        data.add(stored)
        data.commit()
        before = stored.model_dump()

        response = atr.api._key_for_response(stored)

        assert response is not stored
        assert sqlalchemy.inspect(response).transient
        assert sqlalchemy.inspect(stored).persistent
        assert not data.dirty
        assert not data.new
        assert stored.model_dump() == before
        assert response.ascii_armored_key.splitlines()[-2] == "=mT6V"
    engine.dispose()


def test_key_response_identifies_invalid_stored_armor() -> None:
    stored = _certificate()
    stored.ascii_armored_key *= 2

    with pytest.raises(ValueError, match=stored.fingerprint):
        atr.api._key_for_response(stored)


@pytest.mark.parametrize("handler_name", ["committee_keys", "key_get", "keys_user"])
async def test_key_responses_rearmor_without_changing_stored_values(handler_name: str) -> None:
    certificates = [_certificate(), _certificate(pgp_fixtures.RFC9580_V6_PUBLIC_KEY_ASC)]
    before = [key.model_dump(mode="json") for key in certificates]
    data = mock.MagicMock()
    data.signing_certificate.return_value.demand = mock.AsyncMock(return_value=certificates[0])
    data.signing_certificate.return_value.all = mock.AsyncMock(return_value=certificates)
    data.committee.return_value.demand = mock.AsyncMock(
        return_value=sql.Committee(key="example", signing_certificates=certificates)
    )
    args = {
        "committee_keys": ("committee/keys", safe.CommitteeKey("example")),
        "key_get": ("key/get", unsafe.UnsafeStr(certificates[0].fingerprint)),
        "keys_user": ("keys/user", unsafe.UnsafeStr("alice")),
    }

    with mock.patch.object(atr.api.db, "session", return_value=contextlib.nullcontext(data)):
        response, status = await inspect.unwrap(getattr(atr.api, handler_name))(*args[handler_name])

    assert status == 200
    parsed = models.api.ResultsAdapter.validate_python(response)
    assert parsed.model_dump(mode="json") == response
    returned = [response["key"]] if handler_name == "key_get" else response["keys"]
    expected = before[:1] if handler_name == "key_get" else before
    for original, result in zip(expected, returned, strict=True):
        assert result == original | {"ascii_armored_key": pgp.certificate_rearmored(original["ascii_armored_key"])}
    assert returned[0]["ascii_armored_key"].splitlines()[-2] == "=mT6V"
    if len(returned) == 2:
        assert not any(line.startswith("=") for line in returned[1]["ascii_armored_key"].splitlines())
    assert [key.model_dump(mode="json") for key in certificates] == before


async def test_keys_upload_returns_compatible_success_and_error_keys() -> None:
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    writer = keys.CommitteeMember(write, mock.MagicMock(), mock.MagicMock(), "example")
    parsed = writer._CommitteeParticipant__block_models(
        pgp_fixtures.ALL_UIDS_REVOKED_PUBLIC_KEY_ASC, cache.EmailUidLookup({})
    )
    key = parsed[0]
    assert isinstance(key, datatypes.Key)
    before = key.key_model.model_dump(mode="json")
    outcomes = outcome.List[datatypes.Key]()
    outcomes.append_result(key)
    outcomes.append_error(datatypes.PublicKeyError(key, ValueError("rejected")))
    outcomes.append_error(ValueError("no key"))
    write.as_committee_member.return_value.keys.ensure_associated = mock.AsyncMock(return_value=(outcomes, {}))

    with (
        mock.patch.object(atr.api, "_jwt_asf_uid", return_value="alice"),
        mock.patch.object(atr.api.storage, "write", return_value=contextlib.nullcontext(write)),
        mock.patch.object(atr.api.log, "keys_submitted"),
    ):
        response, status = await inspect.unwrap(atr.api.keys_upload)(
            "keys/upload",
            models.api.KeysUploadArgs(filetext=pgp_fixtures.ALL_UIDS_REVOKED_PUBLIC_KEY_ASC, committee="example"),
        )

    assert status == 200
    assert (response["success_count"], response["error_count"]) == (1, 2)
    assert [result["status"] for result in response["results"]] == ["success", "error", "error"]
    for result in response["results"][:2]:
        assert result["key"] == before
        assert any(line.startswith("=") for line in result["key"]["ascii_armored_key"].splitlines())
    assert response["results"][2]["key"] is None
    assert key.key_model.model_dump(mode="json") == before
    assert models.api.validate_keys_upload(response).error_count == 2


def _certificate(block: str = pgp_fixtures.ALL_UIDS_REVOKED_PUBLIC_KEY_ASC) -> sql.SigningCertificate:
    return sql.SigningCertificate(
        fingerprint=pgp.certificate_block_fingerprint(block),
        primary_declared_uid="Alice <alice@example.org>",
        secondary_declared_uids=[],
        apache_uid="alice",
        ascii_armored_key=block,
    )
