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

import unittest.mock as mock
from types import SimpleNamespace
from typing import Final

import atr.config as config
import atr.models.args as args
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post.voting as voting_post
import atr.shared.voting as voting_shared
import atr.storage.writers.vote as vote_writer

INTERNAL_PUBLISH_URL: Final[str] = "https://internal.example.invalid/repos/dist/atr"


async def test_automatic_publish_uses_carried_original_initiator(monkeypatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    release_writer = SimpleNamespace(publish_to_svn=mock.AsyncMock())
    write_as = SimpleNamespace(release=release_writer)
    writer = object.__new__(vote_writer.ReleaseManager)
    writer._ReleaseManager__write_as = write_as
    writer._ReleaseManager__asf_uid = "resolver"
    task = sql.Task(
        status=sql.TaskStatus.QUEUED,
        task_type=sql.TaskType.VOTE_INITIATE,
        task_args={
            "automatic_publish_when_resolved": True,
            "automatic_publish_asf_uid": "alice",
            "initiator_id": "resolver",
            "download_path_suffix": "example-1.0",
        },
        asf_uid="resolver",
    )
    preview_revision = sql.Revision(number="00001", seq=1, release_key="example-1.0", asfuid="resolver")

    enqueue = getattr(writer, "_ReleaseManager__enqueue_automatic_svn_publish")
    await enqueue(
        safe.ProjectKey("example"),
        safe.VersionKey("1.0"),
        preview_revision,
        task,
    )

    release_writer.publish_to_svn.assert_awaited_once()
    assert release_writer.publish_to_svn.await_args.kwargs["publisher_asf_uid"] == "alice"


async def test_email_vote_can_opt_in_to_automatic_publish(monkeypatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    monkeypatch.setattr(voting_post.user, "is_committee_member", lambda _committee, _uid: True)
    form = voting_shared.StartVotingForm.model_validate(
        {
            "csrf_token": "token",
            "email_to": "dev@example.apache.org",
            "vote_duration": 72,
            "subject": "[VOTE] Release example 1.0",
            "subject_template_hash": "abc",
            "body": "Please vote.",
            "automatic_publish_when_resolved": "on",
            "download_path_suffix": "example-1.0",
            "vote_mode": "email",
            "rendered_revision": "00001",
        }
    )
    session = type("Session", (), {"uid": "alice"})()
    committee = sql.Committee(key="example", name="Example", is_podling=False)

    assert await voting_post._publish_opt_in_error(session, form, sql.VoteMode.EMAIL, committee) is None


def test_initiate_args_carry_publish_fields() -> None:
    payload = args.Initiate(
        release_key="example-1.0",
        email_to="dev@example.apache.org",
        vote_duration=72,
        initiator_id="alice",
        initiator_fullname="Alice",
        subject="[VOTE] Release example 1.0",
        body="Please vote.",
        vote_seq=1,
        automatic_publish_when_resolved=True,
        download_path_suffix="example-1.0",
    )
    json_dump = payload.model_dump(mode="json")
    restored = args.Initiate.model_validate(json_dump)

    assert json_dump["automatic_publish_when_resolved"] is True
    assert json_dump["download_path_suffix"] == "example-1.0"
    assert restored.automatic_publish_when_resolved is True
    assert str(restored.download_path_suffix) == "example-1.0"


def test_start_voting_form_publish_on() -> None:
    parsed = voting_shared.StartVotingForm.model_validate(
        {
            "csrf_token": "token",
            "email_to": "dev@example.apache.org",
            "vote_duration": 72,
            "subject": "[VOTE] Release example 1.0",
            "subject_template_hash": "abc",
            "body": "Please vote.",
            "automatic_publish_when_resolved": "on",
            "download_path_suffix": "example-1.0",
            "vote_mode": "trusted",
            "rendered_revision": "00001",
        }
    )

    assert parsed.automatic_publish_when_resolved is True
    assert str(parsed.download_path_suffix) == "example-1.0"
