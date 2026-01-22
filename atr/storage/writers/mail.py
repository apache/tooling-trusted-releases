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

import atr.db as db
import atr.log as log
import atr.mail as mail
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
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid

    async def send(self, message: mail.Message) -> tuple[str, list[str]]:
        is_dev = util.is_dev_environment()

        if is_dev:
            log.info(f"Dev environment detected, not sending email to {message.email_recipient}")
            mid = util.DEV_TEST_MID
            errors: list[str] = []
        else:
            mid, errors = await mail.send(message)

        self.__write_as.append_to_audit_log(
            sent=not is_dev,
            email_sender=message.email_sender,
            email_recipient=message.email_recipient,
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
        committee_name: str,
    ) -> None:
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name


class CommitteeMember(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_name: str,
    ) -> None:
        super().__init__(write, write_as, data, committee_name)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name
