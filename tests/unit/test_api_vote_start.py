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

from types import SimpleNamespace

import pytest

import atr.api as api
import atr.construct as construct
import atr.models as models
import atr.models.safe as safe


@pytest.mark.asyncio
async def test_vote_start_expedited_release_uses_expedited_template(monkeypatch) -> None:
    calls = _patch_rendering(monkeypatch)

    subject, body, fullname = await api._vote_start_subject_and_body(
        _args(subject="Custom subject"), _release(expedited=True), "user", safe.RevisionNumber("00001")
    )

    assert (subject, body, fullname) == ("Custom subject", "RENDERED BODY", "Example User")
    assert calls["subject"] == ""
    assert calls["body"] == construct.START_VOTE_EXPEDITED_DEFAULT
    assert calls["options"].vote_duration == 0


@pytest.mark.asyncio
async def test_vote_start_omitted_subject_and_body_render_policy_templates(monkeypatch) -> None:
    calls = _patch_rendering(monkeypatch)

    subject, body, fullname = await api._vote_start_subject_and_body(
        _args(), _release(), "user", safe.RevisionNumber("00001")
    )

    assert (subject, body, fullname) == ("RENDERED SUBJECT", "RENDERED BODY", "Example User")
    assert calls["subject"] == "[VOTE] {{PROJECT_NAME}} {{VERSION}}"
    assert calls["body"] == "Body {{VERSION}}"
    assert calls["options"].revision_number == safe.RevisionNumber("00001")
    assert calls["options"].vote_duration == 72
    assert calls["options"].fullname == "Example User"


@pytest.mark.asyncio
async def test_vote_start_supplied_subject_and_body_pass_through(monkeypatch) -> None:
    calls = _patch_rendering(monkeypatch)

    subject, body, fullname = await api._vote_start_subject_and_body(
        _args(subject="Custom subject", body="Custom body"), _release(), "user", safe.RevisionNumber("00001")
    )

    assert (subject, body, fullname) == ("Custom subject", "Custom body", "Example User")
    assert calls == {}


def _args(subject: str | None = None, body: str | None = None) -> models.api.VoteStartArgs:
    return models.api.VoteStartArgs(
        project=safe.ProjectKey("example"),
        version=safe.VersionKey("1.0.0"),
        email_to="dev@example.apache.org",
        vote_duration=72,
        subject=subject,
        body=body,
    )


def _patch_rendering(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {}

    async def fake_render(subject: str, body: str, options: construct.StartVoteOptions) -> tuple[str, str]:
        calls["subject"] = subject
        calls["body"] = body
        calls["options"] = options
        return "RENDERED SUBJECT", "RENDERED BODY"

    async def fake_fullname(asf_uid: str) -> str:
        return "Example User"

    monkeypatch.setattr(api, "_ldap_fullname", fake_fullname)
    monkeypatch.setattr(construct, "start_vote_subject_and_body", fake_render)
    return calls


def _release(expedited: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        expedited=expedited,
        project=SimpleNamespace(
            policy_start_vote_subject="[VOTE] {{PROJECT_NAME}} {{VERSION}}",
            policy_start_vote_template="Body {{VERSION}}",
        ),
    )
