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
import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

import atr.models.sql as sql
import atr.post.keys as keys


def test_openpgp_key_uid_warning_allows_matching_asf_uid() -> None:
    warning = keys._openpgp_key_uid_warning(_public_key(apache_uid="alice"), "Alice")

    assert warning is None


def test_openpgp_key_uid_warning_flags_other_asf_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys.util, "as_url", lambda *_args, **_kwargs: "/keys/details/fp")

    warning = keys._openpgp_key_uid_warning(_public_key(apache_uid="bob"), "alice")

    assert warning is not None
    warning_html = str(warning)
    assert "appears to belong to ASF UID bob, not alice" in warning_html
    assert "/keys/details/fp" in warning_html


@pytest.mark.asyncio
async def test_upload_remote_keys_uses_canonical_committee_url(monkeypatch: pytest.MonkeyPatch) -> None:
    committee = SimpleNamespace(key="example", is_podling=True)
    query = SimpleNamespace(get=mock.AsyncMock(return_value=committee))
    data = SimpleNamespace(committee=mock.Mock(return_value=query))

    @contextlib.asynccontextmanager
    async def db_session():
        yield data

    canonical_url = "https://downloads.apache.org/incubator/example/KEYS"
    committee_keys_url = mock.Mock(return_value=canonical_url)
    fetch = mock.AsyncMock(return_value="public keys")
    process = mock.AsyncMock(return_value="rendered")
    monkeypatch.setattr(keys.db, "session", db_session)
    monkeypatch.setattr(keys.paths, "committee_keys_url", committee_keys_url)
    monkeypatch.setattr(keys, "_fetch_keys_from_url", fetch)
    monkeypatch.setattr(keys, "_process_keys", process)
    monkeypatch.setattr(keys.util, "contains_private_key_text", lambda _text: False)

    result = await keys._upload_remote_keys(SimpleNamespace(committee="example"))

    assert result == "rendered"
    committee_keys_url.assert_called_once_with(committee)
    fetch.assert_awaited_once_with(canonical_url)


async def test_fetch_keys_from_url_tolerates_prose_that_is_not_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    async def iter_chunked(_size: int):
        yield b"Ren\xe9 <"
        yield b"r@example.org>\n"

    response = SimpleNamespace(
        content_length=None, content=SimpleNamespace(iter_chunked=iter_chunked), raise_for_status=lambda: None
    )

    @contextlib.asynccontextmanager
    async def get(_url: str, **_kwargs: object):
        yield response

    @contextlib.asynccontextmanager
    async def session(**_kwargs: object):
        yield SimpleNamespace(get=get)

    monkeypatch.setattr(keys.util, "create_secure_session", session)

    assert await keys._fetch_keys_from_url("https://downloads.apache.org/alpha/KEYS") == "Ren\ufffd <r@example.org>\n"


def _public_key(apache_uid: str | None) -> sql.SigningCertificate:
    return sql.SigningCertificate(
        fingerprint="fp",
        algorithm=1,
        length=4096,
        created=datetime.datetime.now(datetime.UTC),
        latest_self_signature=None,
        expires=None,
        primary_declared_uid="Alice <alice@example.org>",
        secondary_declared_uids=[],
        apache_uid=apache_uid,
        ascii_armored_key="-----BEGIN PGP PUBLIC KEY BLOCK-----\nbody\n-----END PGP PUBLIC KEY BLOCK-----\n",
    )
