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


@dataclasses.dataclass
class Message:
    email_sender: str
    email_recipient: str
    subject: str
    body: str
    in_reply_to: str | None = None


async def send(msg_data: Message) -> tuple[str, list[str]]:
    """Send an email notification about an artifact or a vote."""
    log.info(f"Sending email for event: {msg_data}")
    from_addr = msg_data.email_sender
    if not from_addr.endswith(f"@{global_domain}"):
        raise ValueError(f"from_addr must end with @{global_domain}, got {from_addr}")
    to_addr = msg_data.email_recipient
    _validate_recipient(to_addr)

    # UUID4 is entirely random, with no timestamp nor namespace
    # It does have 6 version and variant bits, so only 122 bits are random
    mid = f"{uuid.uuid4()}@{global_domain}"

    # Use EmailMessage with Address objects for CRLF injection protection
    msg = message.EmailMessage(policy=policy.SMTPUTF8)

    try:
        from_local, from_domain = _split_address(from_addr)
        to_local, to_domain = _split_address(to_addr)

        msg["From"] = headerregistry.Address(username=from_local, domain=from_domain)
        msg["To"] = headerregistry.Address(username=to_local, domain=to_domain)
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
    msg.set_content(msg_data.body.strip())

    start = time.perf_counter()
    # Convert to string to satisfy the existing _send_many function signature
    msg_text = msg.as_string()
    log.info(f"sending message: {msg_text}")

    errors = await _send_many(from_addr, [to_addr], msg_text)

    if not errors:
        log.info(f"Sent to {to_addr} successfully")
    else:
        log.warning(f"Errors sending to {to_addr}: {errors}")

    elapsed = time.perf_counter() - start
    log.info(f"Time taken to _send_many: {elapsed:.3f}s")

    return mid, errors


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
    _validate_recipient(to_addr)

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
        hostname=_MAIL_RELAY, port=_SMTP_PORT, timeout=_SMTP_TIMEOUT, tls_context=context
    )
    await smtp.connect()
    log.info(f"Connected to {smtp.hostname}:{smtp.port}")
    await smtp.ehlo()
    await smtp.sendmail(from_addr, [to_addr], msg_bytes)
    await smtp.quit()


def _split_address(addr: str) -> tuple[str, str]:
    """Split an email address into local and domain parts."""
    parts = addr.split("@", 1)
    if len(parts) != 2:
        raise ValueError("Invalid mail address")
    return parts[0], parts[1]


def _validate_recipient(to_addr: str) -> None:
    # Ensure recipient is @apache.org or @tooling.apache.org
    _, domain = _split_address(to_addr)
    domain_is_apache = domain == "apache.org"
    domain_is_subdomain = domain.endswith(".apache.org")
    if not (domain_is_apache or domain_is_subdomain):
        error_msg = f"Email recipient must be @apache.org or @*.apache.org, got {to_addr}"
        log.error(error_msg)
        raise ValueError(error_msg)
