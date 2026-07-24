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

import pydantic
import pytest

import atr.config as config
import atr.shared.projects as projects
import atr.shared.voting as voting

_POLICY_BASE = {
    "csrf_token": "x",
    "variant": "vote",
    "project_key": "test",
    "email_cc": [],
    "email_bcc": [],
    "vote_mode": "email",
    "min_hours": 0,
    "release_checklist": "",
    "vote_comment_template": "",
    "start_vote_subject": "",
    "start_vote_template": "",
    "finish_vote_template": "",
}

_VOTING_BASE = {
    "csrf_token": "x",
    "email_to": "dev@test.apache.org",
    "vote_duration": 0,
    "subject": "[VOTE] Release Apache Test 1.0",
    "subject_template_hash": "abc",
    "body": "Please vote.",
    "vote_mode": "email",
    "rendered_revision": "00001",
}


def test_policy_form_rejects_zero_min_hours_in_production(monkeypatch) -> None:
    monkeypatch.setattr(config, "is_production_mode", lambda: True)
    with pytest.raises(pydantic.ValidationError, match="between 72 and 144"):
        projects.VotePolicyForm.model_validate(_POLICY_BASE)

    monkeypatch.setattr(config, "is_production_mode", lambda: False)
    projects.VotePolicyForm.model_validate(_POLICY_BASE)


def test_voting_form_rejects_zero_vote_duration_in_production(monkeypatch) -> None:
    monkeypatch.setattr(config, "is_production_mode", lambda: True)
    with pytest.raises(pydantic.ValidationError, match="between 72 and 144"):
        voting.StartVotingForm.model_validate(_VOTING_BASE)

    monkeypatch.setattr(config, "is_production_mode", lambda: False)
    voting.StartVotingForm.model_validate(_VOTING_BASE)
