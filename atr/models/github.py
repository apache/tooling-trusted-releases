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

import time

import pydantic

from . import schema


class TrustedPublisherPayload(schema.Subset):
    actor: str
    actor_id: int
    aud: str
    base_ref: str
    check_run_id: str
    enterprise: str
    enterprise_id: str
    event_name: str
    exp: int | None = None
    head_ref: str
    iat: int
    iss: str
    job_workflow_ref: str
    job_workflow_sha: str
    jti: str
    nbf: int | None = None
    ref: str
    ref_protected: str
    ref_type: str
    repository: str
    repository_owner: str
    repository_visibility: str
    run_attempt: str
    run_number: str
    runner_environment: str
    sha: str
    sub: str
    workflow: str
    workflow_ref: str
    workflow_sha: str

    @pydantic.field_validator("exp")
    @classmethod
    def _validate_exp(cls, value: int | None) -> int | None:
        if value is None:
            return value
        now = int(time.time())
        if now > value:
            raise ValueError("Token has expired")
        return value

    @pydantic.field_validator("nbf")
    @classmethod
    def _validate_nbf(cls, value: int | None) -> int | None:
        if value is None:
            return value
        now = int(time.time())
        if value and (now < value):
            raise ValueError("Token not yet valid")
        return value
