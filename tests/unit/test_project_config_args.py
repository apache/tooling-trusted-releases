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

import re

import pydantic
import pytest

import atr.models.api as api


def _project_config(**policy: object) -> api.ProjectConfigArgs:
    return api.ProjectConfigArgs.model_validate(
        {"project_key": "tooling", "committee_key": "tooling", "policy": policy}
    )


def test_project_config_accepts_vote_recipients_on_committee_domain() -> None:
    args = _project_config(vote_recipients={"to": "private@tooling.apache.org", "cc": ["dev@tooling.apache.org"]})

    assert args.policy is not None
    assert args.policy.vote_recipients is not None
    assert args.policy.vote_recipients.to == "private@tooling.apache.org"


def test_project_config_rejects_vote_recipient_on_another_committee() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("must be on 'tooling.apache.org'")):
        _project_config(vote_recipients={"to": "dev@other.apache.org"})


def test_project_config_rejects_vote_recipient_off_foundation() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("must be on 'tooling.apache.org'")):
        _project_config(vote_recipients={"to": "private@tooling.apache.org", "cc": ["someone@example.org"]})


def test_project_config_accepts_announce_recipients_on_any_apache_address() -> None:
    args = _project_config(announce_recipients={"to": "announce@apache.org", "cc": ["dev@other.apache.org"]})

    assert args.policy is not None
    assert args.policy.announce_recipients is not None
    assert args.policy.announce_recipients.to == "announce@apache.org"


def test_project_config_rejects_announce_recipient_off_foundation() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("must be an apache.org address")):
        _project_config(announce_recipients={"to": "someone@example.org"})
