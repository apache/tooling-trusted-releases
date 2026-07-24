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
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pydantic
import pytest

import atr.ldap as ldap
import atr.mail as mail
import atr.models.args as args
import atr.tasks.message as message

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_send_args_accepts_and_rejects_message_id() -> None:
    base = _send_args()
    without_message_id = args.Send.model_validate(base)
    with_message_id = args.Send.model_validate({**base, "message_id": "preallocated@example.apache.org"})

    assert without_message_id.message_id is None
    assert with_message_id.message_id == "preallocated@example.apache.org"
    with pytest.raises(pydantic.ValidationError, match=r"Message ID"):
        args.Send.model_validate({**base, "message_id": "preallocated@example.org"})


@pytest.mark.asyncio
async def test_send_passes_message_id_to_mail_writer(monkeypatch: "MonkeyPatch") -> None:
    monkeypatch.setattr(
        "atr.tasks.message.ldap.account_lookup",
        mock.AsyncMock(
            return_value=ldap.Result(
                dn="uid=validuser,ou=people,dc=apache,dc=org", uid=["validuser"], cn=["Valid User"]
            )
        ),
    )
    mock_mail_send = mock.AsyncMock(return_value=("preallocated@example.apache.org", []))
    mock_wafc = mock.MagicMock()
    mock_wafc.mail.send = mock_mail_send
    mock_write = mock.MagicMock()
    mock_write.as_foundation_committer.return_value = mock_wafc

    @contextlib.asynccontextmanager
    async def mock_storage_write(_asf_uid: str) -> AsyncIterator[mock.MagicMock]:
        yield mock_write

    monkeypatch.setattr("atr.tasks.message.storage.write", mock_storage_write)

    result = await message.send(
        _send_args(email_sender="validuser@apache.org", message_id="preallocated@example.apache.org")
    )
    sent_message = mock_mail_send.call_args.args[0]

    assert result is not None
    assert result.mid == "preallocated@example.apache.org"
    assert sent_message.message_id == "preallocated@example.apache.org"


@pytest.mark.asyncio
async def test_send_raises_when_all_recipients_fail(monkeypatch: "MonkeyPatch") -> None:
    monkeypatch.setattr(
        "atr.tasks.message.ldap.account_lookup",
        mock.AsyncMock(
            return_value=ldap.Result(
                dn="uid=validuser,ou=people,dc=apache,dc=org", uid=["validuser"], cn=["Valid User"]
            )
        ),
    )
    mock_mail_send = mock.AsyncMock(
        return_value=("mid@apache.org", ["failed to send to dev@project.apache.org: connection refused"])
    )
    mock_wafc = mock.MagicMock()
    mock_wafc.mail.send = mock_mail_send
    mock_write = mock.MagicMock()
    mock_write.as_foundation_committer.return_value = mock_wafc

    @contextlib.asynccontextmanager
    async def mock_storage_write(_asf_uid: str) -> AsyncIterator[mock.MagicMock]:
        yield mock_write

    monkeypatch.setattr("atr.tasks.message.storage.write", mock_storage_write)

    with pytest.raises(message.SendError, match=r"any recipient"):
        await message.send(_send_args(email_sender="validuser@apache.org"))


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
async def test_send_returns_warnings_on_partial_failure(monkeypatch: "MonkeyPatch") -> None:
    monkeypatch.setattr(
        "atr.tasks.message.ldap.account_lookup",
        mock.AsyncMock(
            return_value=ldap.Result(
                dn="uid=validuser,ou=people,dc=apache,dc=org", uid=["validuser"], cn=["Valid User"]
            )
        ),
    )
    mock_mail_send = mock.AsyncMock(
        return_value=("mid@apache.org", ["failed to send to other@project.apache.org: connection refused"])
    )
    mock_wafc = mock.MagicMock()
    mock_wafc.mail.send = mock_mail_send
    mock_write = mock.MagicMock()
    mock_write.as_foundation_committer.return_value = mock_wafc

    @contextlib.asynccontextmanager
    async def mock_storage_write(_asf_uid: str) -> AsyncIterator[mock.MagicMock]:
        yield mock_write

    monkeypatch.setattr("atr.tasks.message.storage.write", mock_storage_write)

    result = await message.send(
        {**_send_args(email_sender="validuser@apache.org"), "email_cc": ["other@project.apache.org"]}
    )

    assert result is not None
    assert result.mail_send_warnings == ["failed to send to other@project.apache.org: connection refused"]


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


def test_send_task_args_omits_only_empty_message_id() -> None:
    base = args.Send.model_validate(_send_args())
    with_message_id = args.Send.model_validate(_send_args(message_id="preallocated@example.apache.org"))

    without_message_id = base.as_task_args()
    with_supplied_message_id = with_message_id.as_task_args()

    assert "message_id" not in without_message_id
    assert "in_reply_to" in without_message_id
    assert without_message_id["in_reply_to"] is None
    assert with_supplied_message_id["message_id"] == "preallocated@example.apache.org"


def _send_args(
    email_sender: str = "validuser@apache.org",
    email_to: str = "dev@project.apache.org",
    subject: str = "Test Subject",
    body: str = "Test body",
    in_reply_to: str | None = None,
    message_id: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "email_sender": email_sender,
        "email_to": email_to,
        "subject": subject,
        "body": body,
        "in_reply_to": in_reply_to,
        "footer_category": mail.MailFooterCategory.NONE,
    }
    if message_id is not None:
        result["message_id"] = message_id
    return result
