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

"""Tests for ASF ID validation in atr.tasks.message module."""

import contextlib
import unittest.mock as mock
from typing import TYPE_CHECKING

import pydantic
import pytest

import atr.ldap as ldap
import atr.mail as mail
import atr.tasks.message as message

if TYPE_CHECKING:
    from pytest import MonkeyPatch


@pytest.mark.asyncio
async def test_send_rejects_banned_asf_account(monkeypatch: "MonkeyPatch") -> None:
    """Test that a banned ASF account raises SendError."""
    monkeypatch.setattr(
        "atr.tasks.message.ldap.account_lookup",
        mock.AsyncMock(
            return_value=ldap.Result(
                dn="uid=banneduser,ou=people,dc=apache,dc=org",
                uid=["banneduser"],
                cn=["Banned User"],
                asf_banned=["yes"],
            )
        ),
    )

    with pytest.raises(message.SendError, match=r"banned"):
        await message.send(_send_args(email_sender="banneduser@apache.org"))


@pytest.mark.asyncio
async def test_send_rejects_bare_invalid_asf_id(monkeypatch: "MonkeyPatch") -> None:
    """Test that a bare ASF UID (no @) not found in LDAP raises SendError."""
    monkeypatch.setattr("atr.tasks.message.ldap.account_lookup", mock.AsyncMock(return_value=None))

    with pytest.raises(pydantic.ValidationError, match=r"not a valid email address"):
        await message.send(_send_args(email_sender="nosuchuser"))


@pytest.mark.asyncio
async def test_send_rejects_invalid_asf_id(monkeypatch: "MonkeyPatch") -> None:
    """Test that an ASF UID not found in LDAP raises SendError."""
    # ldap.account_lookup returns None for an unknown UID
    monkeypatch.setattr("atr.tasks.message.ldap.account_lookup", mock.AsyncMock(return_value=None))

    with pytest.raises(message.SendError, match=r"Invalid email account"):
        await message.send(_send_args(email_sender="nosuchuser@apache.org"))


@pytest.mark.asyncio
async def test_send_succeeds_with_valid_asf_id(monkeypatch: "MonkeyPatch") -> None:
    """Test that a valid ASF UID passes LDAP validation and sends the email."""
    # ldap.account_lookup returns a dict for a known UID
    monkeypatch.setattr(
        "atr.tasks.message.ldap.account_lookup",
        mock.AsyncMock(
            return_value=ldap.Result(
                dn="uid=validuser,ou=people,dc=apache,dc=org", uid=["validuser"], cn=["Valid User"]
            )
        ),
    )

    # Mock the storage.write async context manager chain:
    #   storage.write(uid) -> write -> write.as_foundation_committer() -> wafc -> wafc.mail.send() -> (mid, [])
    mock_mail_send = mock.AsyncMock(return_value=("test-mid@apache.org", []))
    mock_wafc = mock.MagicMock()
    mock_wafc.mail.send = mock_mail_send
    mock_write = mock.MagicMock()
    mock_write.as_foundation_committer.return_value = mock_wafc

    @contextlib.asynccontextmanager
    async def mock_storage_write(_asf_uid: str):  # type: ignore[no-untyped-def]
        yield mock_write

    monkeypatch.setattr("atr.tasks.message.storage.write", mock_storage_write)

    result = await message.send(_send_args(email_sender="validuser@apache.org"))

    # Verify the result
    assert result is not None
    assert result.mid == "test-mid@apache.org"
    assert result.mail_send_warnings == []

    # Verify mail.send was called exactly once
    mock_mail_send.assert_called_once()


"""Build an argument dict matching the Send schema."""


def _send_args(
    email_sender: str = "validuser@apache.org",
    email_recipient: str = "dev@project.apache.org",
    subject: str = "Test Subject",
    body: str = "Test body",
    in_reply_to: str | None = None,
) -> dict[str, str | None]:
    return {
        "email_sender": email_sender,
        "email_recipient": email_recipient,
        "subject": subject,
        "body": body,
        "in_reply_to": in_reply_to,
        "footer_category": mail.MailFooterCategory.NONE,
    }
