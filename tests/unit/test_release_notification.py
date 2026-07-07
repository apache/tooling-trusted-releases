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

import datetime

import atr.construct as construct
import atr.mail as mail
import atr.models.args as args
import atr.models.sql as sql


def _notification() -> args.Send:
    committee = sql.Committee(name="Apache Example", key="example")
    project = sql.Project(name="Apache Example", key="example")
    released = datetime.datetime(2026, 7, 3, 12, 0, 0, tzinfo=datetime.UTC)
    return construct.release_notification(committee, project, "1.2.3", released)


def test_release_notification_goes_to_the_releases_list_from_noreply():
    send = _notification()

    assert send.email_to == "releases@tooling.apache.org"
    assert send.email_sender == mail.NOREPLY_EMAIL_ADDRESS
    assert send.footer_category == mail.MailFooterCategory.AUTO


def test_release_notification_subject_names_committee_project_and_version():
    send = _notification()

    assert send.subject == "Apache Example Released Example 1.2.3"


def test_release_notification_body_carries_the_version_and_a_catalogue_link():
    send = _notification()

    assert "1.2.3" in send.body
    assert "/catalog/example" in send.body
