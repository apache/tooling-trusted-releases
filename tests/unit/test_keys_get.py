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
import types
import unittest.mock as mock

import asfquart.session
import pytest
import quart

import atr.blueprints.get
import atr.get.keys as keys
import atr.models.sql as sql
import atr.pgp as pgp
import tests.unit.pgp_fixtures as pgp_fixtures


@pytest.mark.parametrize("as_bytes", [False, True])
async def test_details_rearmors_display_without_changing_stored_text(as_bytes: bool) -> None:
    original = pgp_fixtures.ALL_UIDS_REVOKED_PUBLIC_KEY_ASC
    stored = original.encode() if as_bytes else original
    key = sql.SigningCertificate(
        fingerprint=pgp.certificate_block_fingerprint(original),
        primary_declared_uid="Alice <alice@example.org>",
        ascii_armored_key=stored,
    )
    data = mock.MagicMock()
    data.signing_certificate.return_value.get = mock.AsyncMock(return_value=key)
    data.execute = mock.AsyncMock(return_value=mock.MagicMock())
    data.execute.return_value.scalars.return_value.all.return_value = []
    viewer = types.SimpleNamespace(uid="viewer", member_committees=[], participant_committees=[], is_admin=True)
    app = quart.Quart(__name__)
    app.register_blueprint(atr.blueprints.get._BLUEPRINT)

    with (
        mock.patch.object(asfquart.session, "read", new=mock.AsyncMock(return_value=viewer)),
        mock.patch.object(atr.blueprints.get.common, "authenticate", new=mock.AsyncMock(return_value=viewer)),
        mock.patch.object(keys.db, "session", return_value=contextlib.nullcontext(data)),
        mock.patch.object(keys.template, "blank", new=_render_content),
    ):
        response = await app.test_client().get(f"/keys/details/{key.fingerprint}")

    assert response.status_code == 200
    assert pgp.certificate_rearmored(original) in await response.get_data(as_text=True)
    assert key.ascii_armored_key == stored


async def _render_content(_title: str, **context: object) -> str:
    return str(context["content"])
