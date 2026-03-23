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

import dataclasses
import email.headerregistry as headerregistry
import email.message as message
import email.policy as policy
import email.utils as utils
import enum
import ssl
import time
import uuid
from typing import Final

import aiosmtplib

# import dkim
import atr.log as log

# TODO: We should choose a pattern for globals
# We could e.g. use uppercase instead of global_
# It's not always worth identifying globals as globals
# But in many cases we should do so
global_domain: str = "apache.org"

_MAIL_RELAY: Final[str] = "mail-relay.apache.org"
_SMTP_PORT: Final[int] = 587
_SMTP_TIMEOUT: Final[int] = 30


class MailFooterCategory(enum.StrEnum):
    NONE = "none"
    USER = "user"
    AUTO = "auto"


@dataclasses.dataclass
class Message:
    email_sender: str
    email_recipient: str
    subject: str
    body: str
    email_cc: str = ""
    email_bcc: str = ""
    in_reply_to: str | None = None


async def send(msg_data: Message, category: MailFooterCategory) -> tuple[str, list[str]]:
    """Send an email notification about an artifact or a vote."""
    log.info(f"Sending email for event: {msg_data}")
    _reject_null_bytes(
        msg_data.email_sender,
        msg_data.email_recipient,
        msg_data.email_cc,
        msg_data.email_bcc,
        msg_data.subject,
        msg_data.body,
        msg_data.in_reply_to,
    )
    from_addr = msg_data.email_sender
    if not from_addr.endswith(f"@{global_domain}"):
        raise ValueError(f"from_addr must end with @{global_domain}, got {from_addr}")
    to_addrs = _validate_recipients(msg_data.email_recipient)
    if len(to_addrs) < 1:
        raise ValueError("email_recipient must contain at least one email address")
    cc_addrs = _validate_recipients(msg_data.email_cc)
    bcc_addrs = _validate_recipients(msg_data.email_bcc)

    # UUID4 is entirely random, with no timestamp nor namespace
    # It does have 6 version and variant bits, so only 122 bits are random
    mid = f"{uuid.uuid4()}@{global_domain}"

    # Use EmailMessage with Address objects for CRLF injection protection
    msg = message.EmailMessage(policy=policy.SMTPUTF8)

    try:
        from_local, from_domain = _split_address(from_addr)

        msg["From"] = headerregistry.Address(username=from_local, domain=from_domain)
        msg["To"] = tuple(
            headerregistry.Address(username=local, domain=domain)
            for local, domain in [_split_address(addr) for addr in to_addrs]
        )
        if cc_addrs:
            msg["Cc"] = tuple(
                headerregistry.Address(username=local, domain=domain)
                for local, domain in [_split_address(addr) for addr in cc_addrs]
            )
        msg["Subject"] = msg_data.subject
        msg["Date"] = utils.formatdate(usegmt=True)
        msg["Message-ID"] = f"<{mid}>"

        if msg_data.in_reply_to is not None:
            msg["In-Reply-To"] = f"<{msg_data.in_reply_to}>"
            # TODO: Add message.references if necessary
            msg["References"] = f"<{msg_data.in_reply_to}>"
    except ValueError as e:
        log.error(f"SECURITY: CRLF injection attempt detected in email headers: {e}")
        return mid, [f"CRLF injection detected: {e}"]

    # Set the email body (handles RFC-compliant line endings automatically)
    msg.set_content(_body_with_footer(msg_data.body.strip(), category, from_addr))

    start = time.perf_counter()
    # Convert to string to satisfy the existing _send_many function signature
    msg_text = msg.as_string()
    log.info(f"sending message: {msg_text}")

    # BCC recipients go in the SMTP envelope only, never in message headers
    all_envelope_recipients = to_addrs + cc_addrs + bcc_addrs
    errors = await _send_many(from_addr, all_envelope_recipients, msg_text)

    if not errors:
        log.info(f"Sent to {all_envelope_recipients} successfully")
    else:
        log.warning(f"Errors sending to {all_envelope_recipients}: {errors}")

    elapsed = time.perf_counter() - start
    log.info(f"Time taken to _send_many: {elapsed:.3f}s")

    return mid, errors


def _body_with_footer(body: str, category: MailFooterCategory, from_addr: str) -> str:
    # TODO: AM need to get the domain but we don't have that apart from in a request context currently.
    match category:
        case MailFooterCategory.NONE:
            return body
        case MailFooterCategory.USER:
            asf_uid, _ = _split_address(from_addr)
            return f"{body}\n\nThis email was sent by {asf_uid}@apache.org on the Apache Trusted Releases platform"
        case MailFooterCategory.AUTO:
            return f"{body}\n\nThis email was sent from automation on the Apache Trusted Releases platform"


def _reject_null_bytes(*values: str | None) -> None:
    for value in values:
        if (value is not None) and ("\x00" in value):
            raise ValueError("Email content cannot contain null bytes")


async def _send_many(from_addr: str, to_addrs: list[str], msg_text: str) -> list[str]:
    """Send an email to multiple recipients."""
    message_bytes = bytes(msg_text, "utf-8")

    errors = []
    for addr in to_addrs:
        try:
            await _send_via_relay(from_addr, addr, message_bytes)
        except Exception as e:
            log.exception(f"Failed to send to {addr}:")
            errors.append(f"failed to send to {addr}: {e}")

    return errors


async def _send_via_relay(from_addr: str, to_addr: str, msg_bytes: bytes) -> None:
    """Send an email to a single recipient via the ASF mail relay."""
    # Connect to the ASF mail relay
    # NOTE: Our code is very different from the asfpy code:
    # - Uses types
    # - Uses asyncio
    # Due to the divergence, we should probably not contribute upstream
    # In effect, these are two different "packages" of functionality
    # We can't even sign it first and pass it to asfpy, due to its different design
    log.info(f"Connecting async to {_MAIL_RELAY}:{_SMTP_PORT}")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2

    smtp = aiosmtplib.SMTP(
        hostname=_MAIL_RELAY, port=_SMTP_PORT, timeout=_SMTP_TIMEOUT, tls_context=context, start_tls=True
    )
    await smtp.connect()
    log.info(f"Connected to {smtp.hostname}:{smtp.port}")
    await smtp.ehlo()
    await smtp.sendmail(from_addr, [to_addr], msg_bytes)
    await smtp.quit()


def _split_address(addr: str) -> tuple[str, str]:
    """Split an email address into local and domain parts."""
    if "\x00" in addr:
        raise ValueError("Email address cannot contain null bytes")
    if ("\r" in addr) or ("\n" in addr):
        raise ValueError("Email address cannot contain CR/LF characters")
    parts = addr.split("@", 1)
    if len(parts) != 2:
        raise ValueError("Invalid mail address")
    return parts[0], parts[1]


def _validate_recipients(to_addr: str) -> list[str]:
    # Ensure recipient is @apache.org or @tooling.apache.org
    if not to_addr.strip():
        return []
    emails: list[str] = []
    for mail in to_addr.split(","):
        mail = mail.strip()
        _, domain = _split_address(mail)
        domain_is_apache = domain == "apache.org"
        domain_is_subdomain = domain.endswith(".apache.org")
        if not (domain_is_apache or domain_is_subdomain):
            error_msg = f"Email recipient must be @apache.org or @*.apache.org, got {mail}"
            log.error(error_msg)
            raise ValueError(error_msg)
        emails.append(mail)
    return emails
