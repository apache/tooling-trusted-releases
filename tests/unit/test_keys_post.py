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
async def test_upload_rejects_reflect_mode_committee(monkeypatch: pytest.MonkeyPatch) -> None:
    committee = SimpleNamespace(key="example", display_name="Example", keys_mode=sql.KeysMode.REFLECT)
    query = SimpleNamespace(get=mock.AsyncMock(return_value=committee))
    data = SimpleNamespace(committee=mock.Mock(return_value=query))

    @contextlib.asynccontextmanager
    async def db_session():
        yield data

    monkeypatch.setattr(keys.db, "session", db_session)

    error = await keys._upload_committee_error("example")

    assert error is not None
    assert "imported from SVN" in error


@pytest.mark.asyncio
async def test_upload_accepts_manual_mode_committee(monkeypatch: pytest.MonkeyPatch) -> None:
    committee = SimpleNamespace(key="example", display_name="Example", keys_mode=sql.KeysMode.MANUAL)
    query = SimpleNamespace(get=mock.AsyncMock(return_value=committee))
    data = SimpleNamespace(committee=mock.Mock(return_value=query))

    @contextlib.asynccontextmanager
    async def db_session():
        yield data

    monkeypatch.setattr(keys.db, "session", db_session)

    assert await keys._upload_committee_error("example") is None


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
