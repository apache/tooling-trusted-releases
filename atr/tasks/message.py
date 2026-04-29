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
    if "@" not in task_args.email_sender:
        log.warning(f"Invalid email sender: {task_args.email_sender}")
        sender_asf_uid = task_args.email_sender
    elif task_args.email_sender.endswith("@apache.org"):
        sender_asf_uid = task_args.email_sender.split("@")[0]
    else:
        raise SendError(f"Invalid email sender: {task_args.email_sender}")

    sender_account = await ldap.account_lookup(sender_asf_uid)
    if sender_account is None:
        raise SendError(f"Invalid email account: {task_args.email_sender}")
    if ldap.is_banned(sender_account):
        raise SendError(f"Email account {task_args.email_sender} is banned")

    all_recipients = [task_args.email_to, *task_args.email_cc, *task_args.email_bcc]
    for addr in all_recipients:
        recipient_domain = addr.split("@")[-1]
        sending_to_self = addr == f"{sender_asf_uid}@apache.org"
        # audit_guidance we intentionally allow users to send messages to committees they are not a part of
        sending_to_committee = recipient_domain.endswith(".apache.org")
        if not (sending_to_self or sending_to_committee):
            raise SendError(f"You are not permitted to send emails to {addr}")

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

    async with storage.write(sender_asf_uid) as write:
        wafc = write.as_foundation_committer()
        mid, mail_errors = await wafc.mail.send(message, footer_category)

    if mail_errors:
        log.warning(f"Mail sending to {task_args.email_to} for subject '{task_args.subject}' encountered errors:")
        for error in mail_errors:
            log.warning(f"- {error}")

    # TODO: Record the vote in the database?
    # We'd need to sync with manual votes too
    return results.MessageSend(
        kind="message_send",
        mid=mid,
        mail_send_warnings=mail_errors,
    )
