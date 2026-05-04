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

from __future__ import annotations

import datetime
import email.utils
import pathlib

import aiofiles
import aiofiles.os

import atr.config as config
import atr.db as db
import atr.log as log
import atr.mail as mail
import atr.models.mail as models_mail
import atr.storage as storage
import atr.util as util


class GeneralPublic:
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsGeneralPublic,
        data: db.Session,
    ) -> None:
        self.__write = write
        self.__write_as = write_as
        self.__data = data


class FoundationCommitter(GeneralPublic):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsFoundationCommitter,
        data: db.Session,
    ) -> None:
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid

    async def send(self, message: mail.Message, category: mail.MailFooterCategory) -> tuple[str, list[str]]:
        models_mail.message_id_validate(message.message_id)
        is_dev = config.is_dev_environment()

        if is_dev:
            log.info(f"Dev environment detected, not sending email to {message.email_to}")
            mid = message.message_id if (message.message_id is not None) else util.DEV_TEST_MID
            await _dev_email_log_append(message, category, mid)
            errors: list[str] = []
        else:
            mid, errors = await mail.send(message, category)

        self.__write_as.append_to_audit_log(
            sent=not is_dev,
            email_sender=message.email_sender,
            email_to=message.email_to,
            subject=message.subject,
            mid=mid,
            in_reply_to=message.in_reply_to,
        )

        return mid, errors


class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_key: str,
    ) -> None:
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key


class CommitteeMember(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_key: str,
    ) -> None:
        super().__init__(write, write_as, data, committee_key)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key


async def _dev_email_log_append(message: mail.Message, category: mail.MailFooterCategory, mid: str) -> None:
    log_path = pathlib.Path(config.get().STATE_DIR) / "logs" / "sent-email-dev.log"
    await aiofiles.os.makedirs(log_path.parent, exist_ok=True)
    all_recipients = [message.email_to, *message.email_cc, *message.email_bcc]
    timestamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    body = mail.body_with_footer(message.body.strip(), category, message.email_sender)
    headers = [
        f"Envelope-Date: {timestamp}",
        f"Envelope-From: {message.email_sender}",
        f"Envelope-To: {', '.join(all_recipients)}",
        f"From: {message.email_sender}",
        f"To: {message.email_to}",
        f"Subject: {message.subject}",
        f"Date: {email.utils.formatdate(usegmt=True)}",
        f"Message-ID: <{mid}>",
    ]
    if message.email_cc:
        headers.append(f"Cc: {', '.join(message.email_cc)}")
    if message.email_bcc:
        headers.append(f"Bcc: {', '.join(message.email_bcc)}")
    if message.in_reply_to is not None:
        headers.append(f"In-Reply-To: <{message.in_reply_to}>")
        headers.append(f"References: <{message.in_reply_to}>")
    async with aiofiles.open(log_path, "a", encoding="utf-8") as f:
        await f.write("\n".join(headers))
        await f.write("\n\n")
        await f.write(body)
        await f.write("\n\n")
