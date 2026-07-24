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

"""Tests for CRLF injection protection in atr.mail module."""

import email.message as emailmessage
import email.policy as policy
import unittest.mock as mock
from typing import TYPE_CHECKING

import aiosmtplib
import pytest

import atr.mail as mail
import atr.models.mail as models_mail
import atr.storage.writers.mail as mail_writer
import atr.util as util

if TYPE_CHECKING:
    from pytest import MonkeyPatch


@pytest.mark.asyncio
async def test_address_objects_used_for_from_to_headers(monkeypatch: "MonkeyPatch") -> None:
    """Test that Address objects are used for From/To headers."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    legitimate_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Test Subject",
        body="Test body",
    )

    _, errors = await mail.send(legitimate_message, mail.MailFooterCategory.NONE)

    # Verify the message was sent successfully
    assert len(errors) == 0
    mock_send_many.assert_called_once()

    # Verify the generated email bytes contain properly formatted addresses
    call_args = mock_send_many.call_args
    msg_text = call_args[0][2]  # already a str

    # Address objects format email addresses properly
    assert "From: sender@apache.org" in msg_text
    assert "To: recipient@apache.org" in msg_text


@pytest.mark.asyncio
async def test_footer_auto_appended_to_body(monkeypatch: "MonkeyPatch") -> None:
    """Test that AUTO category appends an automation footer without a user attribution."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Footer test",
        body="Hello.",
    )

    _, errors = await mail.send(msg, mail.MailFooterCategory.AUTO)

    assert len(errors) == 0
    msg_text = mock_send_many.call_args[0][2]
    assert "This email was sent from automation on the Apache Trusted Releases platform" in msg_text


@pytest.mark.asyncio
async def test_footer_user_appended_to_body(monkeypatch: "MonkeyPatch") -> None:
    """Test that USER category appends a footer attributing the sending user."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="bob@apache.org",
        email_to="recipient@apache.org",
        subject="Footer test",
        body="Hello.",
    )

    _, errors = await mail.send(msg, mail.MailFooterCategory.USER)

    assert len(errors) == 0
    msg_text = mock_send_many.call_args[0][2]
    assert "This email was sent by bob@apache.org on the Apache Trusted Releases platform" in msg_text


@pytest.mark.asyncio
async def test_foundation_committer_send_dev_preserves_supplied_message_id(monkeypatch: "MonkeyPatch") -> None:
    monkeypatch.setattr("atr.storage.writers.mail.config.is_dev_environment", lambda: True)
    writer, write_as = _mail_writer()

    mid, errors = await writer.send(
        mail.Message(
            email_sender="sender@apache.org",
            email_to="recipient@apache.org",
            subject="Dev ID test",
            body="Hello.",
            message_id="preallocated@example.apache.org",
        ),
        mail.MailFooterCategory.NONE,
    )

    assert errors == []
    assert mid == "preallocated@example.apache.org"
    assert write_as.append_to_audit_log.call_args.kwargs["mid"] == "preallocated@example.apache.org"


@pytest.mark.asyncio
async def test_foundation_committer_send_dev_rejects_invalid_message_id(monkeypatch: "MonkeyPatch") -> None:
    monkeypatch.setattr("atr.storage.writers.mail.config.is_dev_environment", lambda: True)
    writer, write_as = _mail_writer()

    with pytest.raises(ValueError, match=r"Message ID"):
        await writer.send(
            mail.Message(
                email_sender="sender@apache.org",
                email_to="recipient@apache.org",
                subject="Dev ID test",
                body="Hello.",
                message_id="<preallocated@example.apache.org>",
            ),
            mail.MailFooterCategory.NONE,
        )

    write_as.append_to_audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_foundation_committer_send_dev_uses_test_id_without_supplied_message_id(
    monkeypatch: "MonkeyPatch",
) -> None:
    monkeypatch.setattr("atr.storage.writers.mail.config.is_dev_environment", lambda: True)
    writer, write_as = _mail_writer()

    mid, errors = await writer.send(
        mail.Message(
            email_sender="sender@apache.org",
            email_to="recipient@apache.org",
            subject="Dev ID test",
            body="Hello.",
        ),
        mail.MailFooterCategory.NONE,
    )

    assert errors == []
    assert mid == util.DEV_TEST_MID
    assert write_as.append_to_audit_log.call_args.kwargs["mid"] == util.DEV_TEST_MID


@pytest.mark.asyncio
async def test_foundation_committer_send_records_errors_in_audit_log(monkeypatch: "MonkeyPatch") -> None:
    monkeypatch.setattr("atr.storage.writers.mail.config.is_dev_environment", lambda: False)
    monkeypatch.setattr(
        "atr.storage.writers.mail.mail.send",
        mock.AsyncMock(return_value=("mid@apache.org", ["failed to send to recipient@apache.org: boom"])),
    )
    writer, write_as = _mail_writer()

    mid, errors = await writer.send(
        mail.Message(
            email_sender="sender@apache.org",
            email_to="recipient@apache.org",
            subject="Audit test",
            body="Hello.",
        ),
        mail.MailFooterCategory.NONE,
    )

    assert mid == "mid@apache.org"
    assert errors == ["failed to send to recipient@apache.org: boom"]
    audit_kwargs = write_as.append_to_audit_log.call_args.kwargs
    assert audit_kwargs["sent"] is True
    assert audit_kwargs["errors"] == "failed to send to recipient@apache.org: boom"


@pytest.mark.asyncio
async def test_relay_reachable_false_when_connect_fails(monkeypatch: "MonkeyPatch") -> None:
    smtp = mock.MagicMock()
    smtp.connect = mock.AsyncMock(side_effect=OSError("connection refused"))
    monkeypatch.setattr("atr.mail.aiosmtplib.SMTP", mock.MagicMock(return_value=smtp))

    assert await mail.relay_reachable() is False
    smtp.close.assert_called_once()


@pytest.mark.asyncio
async def test_relay_reachable_false_when_ehlo_fails(monkeypatch: "MonkeyPatch") -> None:
    smtp = mock.MagicMock()
    smtp.connect = mock.AsyncMock()
    smtp.ehlo = mock.AsyncMock(side_effect=aiosmtplib.SMTPHeloError(550, "denied"))
    monkeypatch.setattr("atr.mail.aiosmtplib.SMTP", mock.MagicMock(return_value=smtp))

    assert await mail.relay_reachable() is False
    smtp.close.assert_called_once()


@pytest.mark.asyncio
async def test_relay_reachable_true_when_ehlo_succeeds(monkeypatch: "MonkeyPatch") -> None:
    smtp = mock.MagicMock()
    smtp.connect = mock.AsyncMock()
    smtp.ehlo = mock.AsyncMock()
    monkeypatch.setattr("atr.mail.aiosmtplib.SMTP", mock.MagicMock(return_value=smtp))

    assert await mail.relay_reachable() is True
    smtp.close.assert_called_once()


@pytest.mark.asyncio
async def test_send_accepts_legitimate_message(monkeypatch: "MonkeyPatch") -> None:
    """Test that a legitimate message without CRLF is accepted."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a legitimate message without any CRLF injection attempts
    legitimate_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Legitimate Subject",
        body="This is a legitimate test message with no injection attempts.",
    )

    # Call send
    mid, errors = await mail.send(legitimate_message, mail.MailFooterCategory.NONE)

    # Assert that no errors were returned
    assert len(errors) == 0

    # Assert that _send_many was called (email was sent)
    mock_send_many.assert_called_once()

    # Verify the Date header is in GMT
    call_args = mock_send_many.call_args
    msg_text = call_args[0][2]
    date_line = next((line for line in msg_text.splitlines() if line.startswith("Date: ")), "")
    assert date_line.endswith("+0000") or date_line.endswith("GMT")

    # Verify the Message-ID was generated
    assert "@apache.org" in mid


@pytest.mark.asyncio
async def test_send_accepts_message_with_reply_to(monkeypatch: "MonkeyPatch") -> None:
    """Test that a legitimate message with in_reply_to is accepted."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a legitimate message with a valid in_reply_to
    legitimate_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Re: Previous Subject",
        body="This is a reply message.",
        in_reply_to="previous-message-id@apache.org",
    )

    # Call send
    mid, errors = await mail.send(legitimate_message, mail.MailFooterCategory.NONE)

    # Assert that no errors were returned
    assert len(errors) == 0

    # Assert that _send_many was called (email was sent)
    mock_send_many.assert_called_once()

    # Verify the Message-ID was generated
    assert "@apache.org" in mid


@pytest.mark.asyncio
async def test_send_bcc_in_envelope_not_in_headers(monkeypatch: "MonkeyPatch") -> None:
    """Test that BCC addresses are in the SMTP envelope but absent from all message headers."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="BCC test",
        body="Hello.",
        email_bcc=["secret@apache.org"],
    )

    _, errors = await mail.send(msg, mail.MailFooterCategory.NONE)

    assert len(errors) == 0
    call_args = mock_send_many.call_args
    envelope_recipients = call_args[0][1]
    msg_text = call_args[0][2]

    # BCC must be in the SMTP envelope
    assert "secret@apache.org" in envelope_recipients

    # BCC must not appear anywhere in the message headers
    assert "secret@apache.org" not in msg_text


@pytest.mark.asyncio
async def test_send_cc_appears_in_header_and_envelope(monkeypatch: "MonkeyPatch") -> None:
    """Test that CC addresses appear in the Cc header and the SMTP envelope."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="CC test",
        body="Hello.",
        email_cc=["cc@apache.org"],
    )

    _, errors = await mail.send(msg, mail.MailFooterCategory.NONE)

    assert len(errors) == 0
    call_args = mock_send_many.call_args
    envelope_recipients = call_args[0][1]
    msg_text = call_args[0][2]

    # CC must be in the SMTP envelope
    assert "cc@apache.org" in envelope_recipients

    # CC must appear in a Cc header, not only To
    assert "Cc: cc@apache.org" in msg_text


@pytest.mark.asyncio
async def test_send_empty_cc_bcc_omits_cc_header(monkeypatch: "MonkeyPatch") -> None:
    """Test that omitting CC or BCC produces no Cc header and only To recipients in envelope."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="No CC or BCC test",
        body="Hello.",
    )

    _, errors = await mail.send(msg, mail.MailFooterCategory.NONE)

    assert len(errors) == 0
    call_args = mock_send_many.call_args
    envelope_recipients = call_args[0][1]
    msg_text = call_args[0][2]

    assert envelope_recipients == ["recipient@apache.org"]
    assert "Cc:" not in msg_text
    assert "Bcc:" not in msg_text


@pytest.mark.asyncio
async def test_send_generated_message_id_round_trip(monkeypatch: "MonkeyPatch") -> None:
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Generated ID test",
        body="Hello.",
    )

    mid, errors = await mail.send(msg, mail.MailFooterCategory.NONE)

    assert errors == []
    assert mid.endswith("@apache.org")
    assert f"Message-ID: <{mid}>" in mock_send_many.call_args[0][2]


@pytest.mark.asyncio
async def test_send_handles_non_ascii_headers(monkeypatch: "MonkeyPatch") -> None:
    """Test that non-ASCII characters in headers are handled correctly."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a message with non-ASCII characters in the subject
    message_with_unicode = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Test avec Accént",
        body="This message has non-ASCII characters in the subject.",
    )

    # Call send
    _mid, errors = await mail.send(message_with_unicode, mail.MailFooterCategory.NONE)

    # Assert that no errors were returned
    assert len(errors) == 0

    # Assert that _send_many was called with a string (not bytes)
    mock_send_many.assert_called_once()
    call_args = mock_send_many.call_args
    # Third argument should be str
    msg_text = call_args[0][2]
    assert isinstance(msg_text, str)

    # Verify the subject is present in the message
    assert "Subject: Test avec Accént" in msg_text


@pytest.mark.asyncio
async def test_send_rejects_bcc_header_injection(monkeypatch: "MonkeyPatch") -> None:
    """Test a realistic Bcc header injection attack scenario."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a malicious message attempting to inject a Bcc header
    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Important Notice\r\nBcc: attacker@malicious.com\r\nX-Priority: 1",
        body="This message attempts to secretly copy an attacker.",
    )

    # Call send and expect it to catch the injection
    _, errors = await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    # Assert that the function returned an error
    assert len(errors) == 1
    assert "CRLF injection detected" in errors[0]

    # Assert that _send_many was never called
    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_case_insensitive_duplicate(monkeypatch: "MonkeyPatch") -> None:
    """Test that duplicate detection is case-insensitive."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="Recipient@apache.org",
        subject="Dup test",
        body="Hello.",
        email_bcc=["recipient@apache.org"],
    )

    with pytest.raises(ValueError, match=r"Duplicate recipient"):
        await mail.send(msg, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_content_type_injection(monkeypatch: "MonkeyPatch") -> None:
    """Test injection attempting to override Content-Type header."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a malicious message attempting to inject Content-Type
    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Test\r\nContent-Type: text/html\r\n\r\n<html><script>alert('XSS')</script></html>",
        body="Normal body",
    )

    # Call send and expect it to catch the injection
    _, errors = await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    # Assert that the function returned an error
    assert len(errors) == 1
    assert "CRLF injection detected" in errors[0]

    # Assert that _send_many was never called
    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_cr_only_injection(monkeypatch: "MonkeyPatch") -> None:
    """Test that injection with CR only (\\r) is also rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a malicious message with just CR (no LF)
    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Legitimate Subject\rBcc: evil@example.com",
        body="This is a test message",
    )

    # Call send and expect it to catch the injection
    _, errors = await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    # Assert that the function returned an error
    assert len(errors) == 1
    assert "CRLF injection detected" in errors[0]

    # Assert that _send_many was never called
    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_crlf_in_bcc_address(monkeypatch: "MonkeyPatch") -> None:
    """Test that CRLF injection in the BCC address field is rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Test Subject",
        body="This is a test message",
        email_bcc=["bcc@apache.org\r\nTo: interloper@apache.org"],
    )

    with pytest.raises(ValueError, match=r"CR/LF"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_crlf_in_cc_address(monkeypatch: "MonkeyPatch") -> None:
    """Test that CRLF injection in the CC address field is rejected.

    An attacker supplying CC addresses controls what goes into the Cc header,
    so CR/LF must be caught before address objects are constructed.
    """
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Test Subject",
        body="This is a test message",
        email_cc=["cc@apache.org\r\nBcc: interloper@apache.org"],
    )

    with pytest.raises(ValueError, match=r"CR/LF"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_crlf_in_from_address(monkeypatch: "MonkeyPatch") -> None:
    """Test that CRLF injection in from address field is rejected.

    Note: The from_addr validation happens before EmailMessage processing,
    so this test verifies the early validation layer also protects against injection.
    """
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a malicious message with CRLF in the from address
    malicious_message = mail.Message(
        email_sender="sender@apache.org\r\nBcc: evil@example.com",
        email_to="recipient@apache.org",
        subject="Test Subject",
        body="This is a test message",
    )

    # Call send and expect it to raise ValueError due to invalid from_addr format
    with pytest.raises(ValueError, match=r"from_addr must end with @apache.org"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    # Assert that _send_many was never called
    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_crlf_in_reply_to(monkeypatch: "MonkeyPatch") -> None:
    """Test that CRLF injection in in_reply_to field is rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a malicious message with CRLF in the in_reply_to field
    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Test Subject",
        body="This is a test message",
        in_reply_to="valid-id@apache.org\r\nBcc: evil@example.com",
    )

    # Call send and expect it to catch the injection
    _, errors = await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    # Assert that the function returned an error
    assert len(errors) == 1
    assert "CRLF injection detected" in errors[0]

    # Assert that _send_many was never called
    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_crlf_in_subject(monkeypatch: "MonkeyPatch") -> None:
    """Test that CRLF injection in subject field is rejected."""
    # Mock _send_many to ensure we never actually send emails
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a malicious message with CRLF in the subject
    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Legitimate Subject\r\nBcc: evil@example.com",
        body="This is a test message",
    )

    # Call send and expect it to catch the injection
    _, errors = await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    # Assert that the function returned an error
    assert len(errors) == 1
    assert "CRLF injection detected" in errors[0]

    # Assert that _send_many was never called (email was not sent)
    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_crlf_in_to_address(monkeypatch: "MonkeyPatch") -> None:
    """Test that CRLF injection in to address field is rejected.

    Note: The _validate_recipient check happens before EmailMessage processing,
    so this test verifies the early validation layer also protects against injection.
    """
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a malicious message with CRLF in the to address
    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org\r\nBcc: interloper@apache.org",
        subject="Test Subject",
        body="This is a test message",
    )

    # Call send and expect it to raise ValueError due to invalid recipient format
    with pytest.raises(ValueError, match=r"CR/LF"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    # Assert that _send_many was never called
    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_duplicate_across_cc_and_bcc(monkeypatch: "MonkeyPatch") -> None:
    """Test that the same address in CC and BCC is rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="sender@apache.org",
        subject="Dup test",
        body="Hello.",
        email_cc=["other@apache.org"],
        email_bcc=["other@apache.org"],
    )

    with pytest.raises(ValueError, match=r"Duplicate recipient"):
        await mail.send(msg, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_duplicate_across_to_and_bcc(monkeypatch: "MonkeyPatch") -> None:
    """Test that the same address in To and BCC is rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Dup test",
        body="Hello.",
        email_bcc=["recipient@apache.org"],
    )

    with pytest.raises(ValueError, match=r"Duplicate recipient"):
        await mail.send(msg, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_duplicate_across_to_and_cc(monkeypatch: "MonkeyPatch") -> None:
    """Test that the same address in To and CC is rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Dup test",
        body="Hello.",
        email_cc=["recipient@apache.org"],
    )

    with pytest.raises(ValueError, match=r"Duplicate recipient"):
        await mail.send(msg, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_empty_to(monkeypatch: "MonkeyPatch") -> None:
    """Test that an empty To list is rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="",
        subject="Empty To test",
        body="Hello.",
    )

    with pytest.raises(ValueError, match=r"At least one To recipient is required"):
        await mail.send(msg, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.parametrize(
    "message_id",
    [
        "",
        "<preallocated@example.apache.org>",
        "preallocated@example.apache.org\r\nBcc: evil@example.com",
        "preallocated example@example.apache.org",
        "preallocated",
        "@apache.org",
        "preallocated@",
        "preallocated@example.org",
        "préallocated@example.apache.org",
        "preallocated,foo@example.apache.org",
        "preallocated(foo)@example.apache.org",
        '"preallocated"@example.apache.org',
        "preallocated@.apache.org",
        "preallocated@example..apache.org",
        "pre\x00allocated@example.apache.org",
    ],
)
def test_send_rejects_invalid_supplied_message_id(message_id: str) -> None:
    with pytest.raises(ValueError, match=r"Message ID"):
        models_mail.message_id_validate(message_id)


@pytest.mark.asyncio
async def test_send_rejects_lf_only_injection(monkeypatch: "MonkeyPatch") -> None:
    """Test that injection with LF only (\\n) is also rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    # Create a malicious message with just LF (no CR)
    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Legitimate Subject\nBcc: evil@example.com",
        body="This is a test message",
    )

    # Call send and expect it to catch the injection
    _, errors = await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    # Assert that the function returned an error
    assert len(errors) == 1
    assert "CRLF injection detected" in errors[0]

    # Assert that _send_many was never called
    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_null_byte_in_bcc(monkeypatch: "MonkeyPatch") -> None:
    """Test that null bytes in the BCC field are rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Test Subject",
        body="This is a test message",
        email_bcc=["bcc\x00evil@apache.org"],
    )

    with pytest.raises(ValueError, match=r"null bytes"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_null_byte_in_body(monkeypatch: "MonkeyPatch") -> None:
    """Test that null bytes in body field are rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Test Subject",
        body="Normal start\x00injected content",
    )

    with pytest.raises(ValueError, match=r"null bytes"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_null_byte_in_cc(monkeypatch: "MonkeyPatch") -> None:
    """Test that null bytes in the CC field are rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Test Subject",
        body="This is a test message",
        email_cc=["cc\x00evil@apache.org"],
    )

    with pytest.raises(ValueError, match=r"null bytes"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_null_byte_in_from_address(monkeypatch: "MonkeyPatch") -> None:
    """Test that null bytes in from address field are rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    malicious_message = mail.Message(
        email_sender="sender\x00evil@apache.org",
        email_to="recipient@apache.org",
        subject="Test Subject",
        body="This is a test message",
    )

    with pytest.raises(ValueError, match=r"null bytes"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_null_byte_in_reply_to(monkeypatch: "MonkeyPatch") -> None:
    """Test that null bytes in in_reply_to field are rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Test Subject",
        body="This is a test message",
        in_reply_to="valid-id\x00injected@apache.org",
    )

    with pytest.raises(ValueError, match=r"null bytes"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_null_byte_in_subject(monkeypatch: "MonkeyPatch") -> None:
    """Test that null bytes in subject field are rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Legitimate Subject\x00Bcc: evil@example.com",
        body="This is a test message",
    )

    with pytest.raises(ValueError, match=r"null bytes"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_null_byte_in_to_address(monkeypatch: "MonkeyPatch") -> None:
    """Test that null bytes in to address field are rejected."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    malicious_message = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient\x00evil@apache.org",
        subject="Test Subject",
        body="This is a test message",
    )

    with pytest.raises(ValueError, match=r"null bytes"):
        await mail.send(malicious_message, mail.MailFooterCategory.NONE)

    mock_send_many.assert_not_called()


@pytest.mark.asyncio
async def test_send_to_with_cc_in_envelope(monkeypatch: "MonkeyPatch") -> None:
    """Test that To and CC addresses both appear in the SMTP envelope."""
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="alice@apache.org",
        subject="Multi-recipient test",
        body="Hello both.",
        email_cc=["bob@apache.org"],
    )

    _, errors = await mail.send(msg, mail.MailFooterCategory.NONE)

    assert len(errors) == 0
    call_args = mock_send_many.call_args
    envelope_recipients = call_args[0][1]
    msg_text = call_args[0][2]

    assert "alice@apache.org" in envelope_recipients
    assert "bob@apache.org" in envelope_recipients

    assert "alice@apache.org" in msg_text
    assert "bob@apache.org" in msg_text


@pytest.mark.asyncio
async def test_send_uses_supplied_message_id(monkeypatch: "MonkeyPatch") -> None:
    mock_send_many = mock.AsyncMock(return_value=[])
    monkeypatch.setattr("atr.mail._send_many", mock_send_many)

    msg = mail.Message(
        email_sender="sender@apache.org",
        email_to="recipient@apache.org",
        subject="Preallocated ID test",
        body="Hello.",
        message_id="preallocated@example.apache.org",
    )

    mid, errors = await mail.send(msg, mail.MailFooterCategory.NONE)

    assert errors == []
    assert mid == "preallocated@example.apache.org"
    assert "Message-ID: <preallocated@example.apache.org>" in mock_send_many.call_args[0][2]
    mock_send_many.assert_called_once()


def test_smtp_policy_vs_smtputf8() -> None:
    """Test that SMTPUTF8 policy is required for proper Unicode handling.

    This demonstrates why we use policy.SMTPUTF8 instead of policy.SMTP.
    SMTP policy encodes non-ASCII characters (like é) using RFC2047 encoding,
    while SMTPUTF8 preserves them directly, which is required for modern SMTP.
    """
    # SMTP policy - would encode non-ASCII with RFC2047 (=?utf-8?q?...?=)
    msg_smtp = emailmessage.EmailMessage(policy=policy.SMTP)
    msg_smtp["From"] = "sender@apache.org"
    msg_smtp["To"] = "recipient@apache.org"
    msg_smtp["Subject"] = "Test avec é"
    msg_smtp.set_content("Body")

    smtp_str = msg_smtp.as_string()
    # SMTP policy encodes non-ASCII, making subjects harder to read
    assert "=?utf-8?" in smtp_str
    assert "Test avec é" not in smtp_str

    # SMTPUTF8 policy - preserves Unicode directly (required for our use case)
    msg_smtputf8 = emailmessage.EmailMessage(policy=policy.SMTPUTF8)
    msg_smtputf8["From"] = "sender@apache.org"
    msg_smtputf8["To"] = "recipient@apache.org"
    msg_smtputf8["Subject"] = "Test avec é"
    msg_smtputf8.set_content("Body")

    smtputf8_str = msg_smtputf8.as_string()
    # SMTPUTF8 preserves the character directly
    assert "Test avec é" in smtputf8_str
    assert "=?utf-8?" not in smtputf8_str


def test_split_address_rejects_cr() -> None:
    """Test that _split_address rejects addresses containing CR."""
    with pytest.raises(ValueError, match=r"CR/LF"):
        mail._split_address("user\r@apache.org")


def test_split_address_rejects_lf() -> None:
    """Test that _split_address rejects addresses containing LF."""
    with pytest.raises(ValueError, match=r"CR/LF"):
        mail._split_address("user\n@apache.org")


def test_split_address_rejects_null_byte() -> None:
    """Test that _split_address rejects addresses containing null bytes."""
    with pytest.raises(ValueError, match=r"null bytes"):
        mail._split_address("user\x00@apache.org")


def _mail_writer() -> tuple[mail_writer.FoundationCommitter, mock.MagicMock]:
    write = mock.MagicMock()
    write.authorisation.asf_uid = "sender"
    write_as = mock.MagicMock()
    data = mock.MagicMock()
    return mail_writer.FoundationCommitter(write, write_as, data), write_as
