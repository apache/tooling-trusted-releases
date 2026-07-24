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

import atr.construct as construct
import atr.shared.projects as projects

_FINISH_BASE = {
    "csrf_token": "x",
    "variant": "finish",
    "project_key": "test",
    "announce_release_subject": "[ANNOUNCE] {{PROJECT_NAME}} {{VERSION}} released",
    "announce_release_template": "{{DOWNLOAD_URL}}\n{{PODLING_DISCLAIMER}}",
}

_VOTE_BASE = {
    "csrf_token": "x",
    "variant": "vote",
    "project_key": "test",
    "email_cc": [],
    "email_bcc": [],
    "vote_mode": "email",
    "min_hours": 72,
    "release_checklist": "Check {{PROJECT_NAME}} {{VERSION}}",
    "vote_comment_template": "",
    "start_vote_subject": "[VOTE] Release {{PROJECT_NAME}} {{VERSION}}",
    "start_vote_template": "The vote is open for {{DURATION}} hours.",
    "finish_vote_template": "The vote {{OUTCOME}}.\n{{ATR_TALLY}}",
}


def test_finish_policy_form_validates_template_variables() -> None:
    projects.FinishPolicyForm.model_validate(_FINISH_BASE)
    with pytest.raises(pydantic.ValidationError, match="Unknown template variable: DISCLAIMER"):
        projects.FinishPolicyForm.model_validate(_FINISH_BASE | {"announce_release_template": "{{DISCLAIMER}}"})


def test_unknown_template_variables() -> None:
    names = construct.ANNOUNCE_SUBJECT_VARIABLE_NAMES
    assert construct.unknown_template_variables("{{PROJECT_NAME}} {{VERSION}}", names) == []
    unknown = construct.unknown_template_variables("{{ZEBRA}} {{APPLE}} {{version}}", names)
    assert unknown == ["APPLE", "ZEBRA", "version"]


def test_validate_template_variables() -> None:
    names = construct.ANNOUNCE_SUBJECT_VARIABLE_NAMES
    assert (
        construct.validate_template_variables("{{PROJECT_NAME}} {{VERSION}}", names) == "{{PROJECT_NAME}} {{VERSION}}"
    )
    with pytest.raises(ValueError, match="Unknown template variable: DISCLAIMER"):
        construct.validate_template_variables("{{DISCLAIMER}}", names)
    with pytest.raises(ValueError, match="Unknown template variables: BAD, WORSE"):
        construct.validate_template_variables("{{BAD}} {{WORSE}}", names)


def test_vote_policy_form_validates_template_variables() -> None:
    projects.VotePolicyForm.model_validate(_VOTE_BASE)
    with pytest.raises(pydantic.ValidationError, match="Unknown template variables: BAD, WORSE"):
        projects.VotePolicyForm.model_validate(_VOTE_BASE | {"start_vote_template": "{{BAD}} {{WORSE}}"})
