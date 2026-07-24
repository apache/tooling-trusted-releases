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
import atr.ldap as ldap
import atr.log as log
import atr.mail as mail
import atr.models.args as args
import atr.models.results as results
import atr.storage as storage
import atr.tasks.checks as checks


class SendError(Exception):
    pass


@checks.with_model(args.Send)
async def send(task_args: args.Send) -> results.Results | None:
    is_noreply = task_args.email_sender == mail.NOREPLY_EMAIL_ADDRESS
    sender_asf_uid = _sender_asf_uid(task_args.email_sender)
    if not is_noreply:
        await _verify_sender_account(sender_asf_uid, task_args.email_sender)
    _verify_recipients(task_args, sender_asf_uid)

    message = mail.Message(
        email_sender=task_args.email_sender,
        email_to=task_args.email_to,
        subject=task_args.subject,
        body=task_args.body,
        in_reply_to=task_args.in_reply_to,
        email_cc=task_args.email_cc,
        email_bcc=task_args.email_bcc,
        message_id=task_args.message_id,
    )
    footer_category = mail.MailFooterCategory(task_args.footer_category)
    mid, mail_errors = await _deliver(message, footer_category, is_noreply, sender_asf_uid)

    if mail_errors:
        log.warning(f"Mail sending to {task_args.email_to} for subject '{task_args.subject}' encountered errors:")
        for error in mail_errors:
            log.warning(f"- {error}")
        recipient_total = 1 + len(task_args.email_cc) + len(task_args.email_bcc)
        if len(mail_errors) >= recipient_total:
            raise SendError(f"Failed to send to any recipient: {'; '.join(mail_errors)}")

    # TODO: Record the vote in the database?
    # We'd need to sync with manual votes too
    return results.MessageSend(
        kind="message_send",
        mid=mid,
        mail_send_warnings=mail_errors,
    )


async def _deliver(
    message: mail.Message,
    footer_category: mail.MailFooterCategory,
    is_noreply: bool,
    sender_asf_uid: str,
) -> tuple[str, list[str]]:
    # noreply is automation, not a person, so it sends under the system identity rather
    # than a committer's, which is exempt from LDAP resolution
    if is_noreply:
        async with storage.write_as_system(storage.WriteAsAutomatedMailService) as system_write:
            return await system_write.mail_send(message, footer_category)
    async with storage.write(sender_asf_uid) as write:
        return await write.as_foundation_committer().mail.send(message, footer_category)


def _sender_asf_uid(email_sender: str) -> str:
    if "@" not in email_sender:
        log.warning(f"Invalid email sender: {email_sender}")
        return email_sender
    if email_sender.endswith("@apache.org"):
        return email_sender.split("@")[0]
    raise SendError(f"Invalid email sender: {email_sender}")


def _verify_recipients(task_args: args.Send, sender_asf_uid: str) -> None:
    all_recipients = [task_args.email_to, *task_args.email_cc, *task_args.email_bcc]
    for addr in all_recipients:
        recipient_domain = addr.split("@")[-1]
        sending_to_self = addr == f"{sender_asf_uid}@apache.org"
        # audit_guidance we intentionally allow users to send messages to committees they are not a part of
        sending_to_committee = recipient_domain.endswith(".apache.org")
        if not (sending_to_self or sending_to_committee):
            raise SendError(f"You are not permitted to send emails to {addr}")


async def _verify_sender_account(sender_asf_uid: str, email_sender: str) -> None:
    sender_account = await ldap.account_lookup(sender_asf_uid)
    if sender_account is None:
        raise SendError(f"Invalid email account: {email_sender}")
    if ldap.is_banned(sender_account):
        raise SendError(f"Email account {email_sender} is banned")
