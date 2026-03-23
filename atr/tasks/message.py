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
from typing import Annotated, Any

import pydantic

import atr.ldap as ldap
import atr.log as log
import atr.mail as mail
import atr.models.results as results
import atr.models.schema as schema
import atr.storage as storage
import atr.tasks.checks as checks


def _ensure_footer_enum(value: Any) -> mail.MailFooterCategory | None:
    if isinstance(value, mail.MailFooterCategory):
        return value
    if isinstance(value, str):
        return mail.MailFooterCategory(value)
    else:
        return None


class Send(schema.Strict):
    """Arguments for the task to send an email."""

    email_sender: pydantic.EmailStr = schema.description("The email address of the sender")
    email_recipient: pydantic.EmailStr = schema.description("The email address of the recipient")
    subject: str = schema.description("The subject of the email")
    body: str = schema.description("The body of the email")
    in_reply_to: str | None = schema.description("The message ID of the email to reply to")
    footer_category: Annotated[mail.MailFooterCategory, pydantic.BeforeValidator(_ensure_footer_enum)] = (
        schema.description("The category of email footer to include")
    )


class SendError(Exception):
    pass


@checks.with_model(Send)
async def send(args: Send) -> results.Results | None:
    if "@" not in args.email_sender:
        log.warning(f"Invalid email sender: {args.email_sender}")
        sender_asf_uid = args.email_sender
    elif args.email_sender.endswith("@apache.org"):
        sender_asf_uid = args.email_sender.split("@")[0]
    else:
        raise SendError(f"Invalid email sender: {args.email_sender}")

    sender_account = await ldap.account_lookup(sender_asf_uid)
    if sender_account is None:
        raise SendError(f"Invalid email account: {args.email_sender}")
    if ldap.is_banned(sender_account):
        raise SendError(f"Email account {args.email_sender} is banned")

    recipient_domain = args.email_recipient.split("@")[-1]
    sending_to_self = args.email_recipient == f"{sender_asf_uid}@apache.org"
    # audit_guidance this application intentionally allows users to send messages to committees they are not a part of
    sending_to_committee = recipient_domain.endswith(".apache.org")
    if not (sending_to_self or sending_to_committee):
        raise SendError(f"You are not permitted to send emails to {args.email_recipient}")

    message = mail.Message(
        email_sender=args.email_sender,
        email_recipient=args.email_recipient,
        subject=args.subject,
        body=args.body,
        in_reply_to=args.in_reply_to,
    )

    footer_category = mail.MailFooterCategory(args.footer_category)

    async with storage.write(sender_asf_uid) as write:
        wafc = write.as_foundation_committer()
        mid, mail_errors = await wafc.mail.send(message, footer_category)

    if mail_errors:
        log.warning(f"Mail sending to {args.email_recipient} for subject '{args.subject}' encountered errors:")
        for error in mail_errors:
            log.warning(f"- {error}")

    # TODO: Record the vote in the database?
    # We'd need to sync with manual votes too
    return results.MessageSend(
        kind="message_send",
        mid=mid,
        mail_send_warnings=mail_errors,
    )
