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

import asfquart.base as base
import pytest

import atr.jwtoken as jwtoken


@pytest.mark.parametrize("event_name", ["push", "release", "workflow_dispatch"])
def test_claims_check_accepts_committer_chosen_events(event_name: str) -> None:
    jwtoken._github_oidc_claims_check(_claims(event_name=event_name))


@pytest.mark.parametrize("event_name", ["pull_request", "pull_request_target", "schedule", "workflow_run", ""])
def test_claims_check_refuses_other_events(event_name: str) -> None:
    with pytest.raises(base.ASFQuartException) as exc_info:
        jwtoken._github_oidc_claims_check(_claims(event_name=event_name))

    assert exc_info.value.errorcode == 401
    assert "push, release, workflow_dispatch" in str(exc_info.value)


def test_claims_check_refuses_a_missing_event() -> None:
    claims = _claims()
    del claims["event_name"]

    with pytest.raises(base.ASFQuartException) as exc_info:
        jwtoken._github_oidc_claims_check(claims)

    assert exc_info.value.errorcode == 401


def test_claims_check_refuses_a_self_hosted_runner() -> None:
    with pytest.raises(base.ASFQuartException) as exc_info:
        jwtoken._github_oidc_claims_check(_claims(runner_environment="self-hosted"))

    assert exc_info.value.errorcode == 401
    assert "runner_environment" in str(exc_info.value)


def _claims(**overrides: str) -> dict[str, str]:
    claims = {
        "enterprise": "the-asf",
        "enterprise_id": "212555",
        "event_name": "push",
        "repository_owner": "apache",
        "runner_environment": "github-hosted",
    }
    return claims | overrides
