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
import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.admin as admin_routes
import atr.blueprints.admin as admin_blueprint
import atr.storage as storage


@pytest.mark.asyncio
async def test_delete_committee_keys_post_surfaces_storage_error(monkeypatch):
    session = mock.MagicMock()
    session.redirect = mock.AsyncMock(return_value="redirected")
    delete_form = admin_routes.DeleteCommitteeKeysForm(
        committee_key="alpha",
        confirm_delete="DELETE KEYS",
        csrf_token="csrf",
    )
    delete_error = storage.AccessError("Failed to remove KEYS file for committee alpha: permission denied")
    keys = SimpleNamespace(delete_committee_keys=mock.AsyncMock(side_effect=delete_error))
    waca = SimpleNamespace(keys=keys)
    write = SimpleNamespace(as_committee_admin=mock.MagicMock(return_value=waca))
    flash = mock.AsyncMock()

    @contextlib.asynccontextmanager
    async def fake_write(_session):
        yield write

    session.form_validate = mock.AsyncMock(return_value=delete_form)

    monkeypatch.setattr(admin_routes.storage, "write", fake_write)
    monkeypatch.setattr(admin_routes.quart, "flash", flash)
    monkeypatch.setattr(admin_blueprint.common, "authenticate", mock.AsyncMock(return_value=session))

    result = await admin_routes.catalog_post()

    assert result == "redirected"
    write.as_committee_admin.assert_called_once_with("alpha")
    keys.delete_committee_keys.assert_awaited_once()
    flash.assert_awaited_once_with(str(delete_error), "error")
    session.redirect.assert_awaited_once_with(admin_routes.catalog_get, tab="committee-keys")
