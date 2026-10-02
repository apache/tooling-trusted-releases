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
import unittest.mock as mock

import htpy
import pydantic
import pytest
import quart

import atr.admin as admin
import atr.form as form
import atr.storage.datatypes as datatypes


@pytest.fixture
def notice_data():
    return {
        "csrf_token": "csrf",
        "variant": "NOTIFY_PAT_OWNERS",
        "revocation_id": "burn-id",
        "subject": " PATs revoked ",
        "body": " Create a new PAT.\nUpdate your scripts. ",
    }


def test_notice_form_normalizes_content(notice_data):
    notice = form.validate(admin.UsersForm, notice_data)
    assert isinstance(notice, admin.NotifyPATOwnersForm)
    assert notice.subject == "PATs revoked"
    assert notice.body == "Create a new PAT.\nUpdate your scripts."


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject", " "),
        ("subject", "Notice\rBcc: other@apache.org"),
        ("subject", "Notice\nBcc: other@apache.org"),
        ("subject", "Notice\x00"),
        ("body", " "),
        ("body", "Notice\x00"),
    ],
)
def test_notice_form_rejects_invalid_content(notice_data, field, value):
    with pytest.raises(pydantic.ValidationError):
        admin.NotifyPATOwnersForm.model_validate(notice_data | {field: value})


@pytest.mark.parametrize("has_tokens", [False, True])
async def test_revocation_redirects_to_its_fixed_audience(monkeypatch, has_tokens):
    revocation = (
        datatypes.PATRevocation(
            id="burn-id",
            actor="admin",
            revoked=datetime.datetime.now(datetime.UTC),
            count=3,
            owners=["admin", "alice"],
        )
        if has_tokens
        else None
    )
    writer = mock.Mock()
    writer.revoke_all_users_tokens = mock.AsyncMock(return_value=revocation)
    writer.pat_revocation = mock.AsyncMock(return_value=revocation)
    write = mock.Mock()
    write.as_foundation_admin.return_value.tokens = writer
    monkeypatch.setattr(admin.storage, "write", lambda _session: contextlib.nullcontext(write))
    monkeypatch.setattr(admin.quart, "flash", mock.AsyncMock())
    monkeypatch.setattr(
        admin.util, "as_url", lambda _view, **args: "/admin?" + "&".join(f"{k}={v}" for k, v in args.items())
    )
    monkeypatch.setattr(form, "csrf_input", lambda: htpy.input(type="hidden", name="csrf_token", value="csrf"))
    monkeypatch.setattr(form, "_get_flash_error_data", mock.AsyncMock(return_value={}))
    session = mock.Mock(asf_uid="admin", redirect=mock.AsyncMock())
    await admin._users_revoke_all_tokens(session)
    writer.revoke_all_users_tokens.assert_awaited_once_with()
    writer.notify_revoked_pat_owners.assert_not_called()
    write.as_foundation_admin.return_value.banner.set_current.assert_not_called()
    if revocation is None:
        session.redirect.assert_awaited_once_with(admin.users_get, tab="revoke-all-tokens")
        return
    session.redirect.assert_awaited_once_with(admin.users_get, tab="revoke-all-tokens", revocation_id="burn-id")
    async with quart.Quart(__name__).test_request_context("/admin?tab=revoke-all-tokens&revocation_id=burn-id"):
        content = str(await admin._users_revoke_all_tokens_tab(session, "burn-id"))
    assert "To: <code>admin@apache.org</code>. BCC: 1 affected user." in content
    assert "Email 2 affected users" in content
    assert 'name="revocation_id"' in content
    assert 'value="burn-id"' in content
    assert 'name="email_to"' not in content
    assert 'name="email_bcc"' not in content
    assert 'href="/admin?tab=banner">change the site banner</a> or do nothing.</p>' in content
    assert content.index("revoked at") < content.index("Please choose whether") < content.index("To: ")
    assert "Done without sending email" not in content
