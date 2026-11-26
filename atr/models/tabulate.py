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

import enum

import pydantic

from . import schema


class Vote(enum.Enum):
    YES = "Yes"
    NO = "No"
    ABSTAIN = "-"
    UNKNOWN = "?"


class VoteStatus(enum.Enum):
    BINDING = "Binding"
    # From a PMC member, but not +1, 0 or -1, so not binding
    FRACTIONAL = "Fractional"
    COMMITTER = "Committer"
    CONTRIBUTOR = "Contributor"
    UNKNOWN = "Unknown"


class VoteEmail(schema.Strict):
    name: str = schema.example("Example User")
    asf_uid_or_email: str = schema.example("user")
    from_email: str = schema.example("user@example.org")
    status: VoteStatus = schema.example(VoteStatus.BINDING)
    asf_eid: str = schema.example("102ed8a-503db792-79bc789-b8ca87ce@apache.org")
    iso_datetime: str = schema.example("2025-05-01T12:00:00Z")
    vote: Vote = schema.example(Vote.YES)
    # A fractional vote as its email wrote it, which counts as an abstention
    fraction: str | None = schema.default_example(None, "+0.5")
    quotation: str = schema.example("+1 (Binding)")
    updated: bool = schema.example(True)
    asf_uid: str | None = schema.default_example(None, "user")
    unixtime: int | None = schema.default_example(None, 1714564800)
    confirmed: bool = schema.default_example(False, True)
    confirmed_by: str | None = schema.default_example(None, "chair")

    @pydantic.field_validator("status", mode="before")
    @classmethod
    def status_to_enum(cls, v):
        return VoteStatus(v) if isinstance(v, str) else v

    @pydantic.field_validator("vote", mode="before")
    @classmethod
    def vote_to_enum(cls, v):
        return Vote(v) if isinstance(v, str) else v


class VoteDetails(schema.Strict):
    start_unixtime: int | None = schema.example(1714435200)
    votes: dict[str, VoteEmail] = schema.example(
        {
            "user": VoteEmail(
                name="Example User",
                asf_uid_or_email="user",
                from_email="user@example.org",
                status=VoteStatus.BINDING,
                asf_eid="102ed8a-503db792-79bc789-b8ca87ce@apache.org",
                iso_datetime="2025-05-01T12:00:00Z",
                vote=Vote.YES,
                quotation="+1 (Binding)",
                updated=True,
            )
        }
    )
    summary: dict[str, int] = schema.example({"user": 1})
