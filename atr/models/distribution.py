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

import pydantic

from . import basic, safe, schema, sql


class ArtifactHubAvailableVersion(schema.Subset):
    ts: int


class ArtifactHubLink(schema.Subset):
    url: str | None = None
    name: str | None = None


class ArtifactHubRepository(schema.Subset):
    name: str | None = None


class ArtifactHubResponse(schema.Subset):
    available_versions: list[ArtifactHubAvailableVersion] = pydantic.Field(default_factory=list)
    home_url: str | None = None
    links: list[ArtifactHubLink] = pydantic.Field(default_factory=list)
    name: str | None = None
    version: str | None = None
    repository: ArtifactHubRepository | None = None


class DockerResponse(schema.Subset):
    tag_last_pushed: str | None = None


class GitHubResponse(schema.Subset):
    published_at: str | None = None
    html_url: str | None = None


class MavenDoc(schema.Subset):
    timestamp: int | None = None


class MavenResponseBody(schema.Subset):
    start: int | None = None
    docs: list[MavenDoc] = pydantic.Field(default_factory=list)


class MavenResponse(schema.Subset):
    response: MavenResponseBody = pydantic.Field(default_factory=MavenResponseBody)


class NpmResponse(schema.Subset):
    name: str | None = None
    time: dict[str, str] = pydantic.Field(default_factory=dict)
    homepage: str | None = None


class PyPIUrl(schema.Subset):
    upload_time_iso_8601: str | None = None
    url: str | None = None


class PyPIInfo(schema.Subset):
    release_url: str | None = None
    project_url: str | None = None


class PyPIResponse(schema.Subset):
    urls: list[PyPIUrl] = pydantic.Field(default_factory=list)
    info: PyPIInfo = pydantic.Field(default_factory=PyPIInfo)


# Lax to ignore csrf_token and submit
# Our previous forms implementation typed platform as Any, which was insufficient
# And this way we also get nice JSON from the Pydantic model dump
# Including all of the enum properties
class Data(schema.Subset):
    platform: sql.DistributionPlatform
    owner_namespace: safe.Alphanumeric | None = None
    package: safe.Alphanumeric
    version: safe.VersionKey
    details: bool

    @pydantic.field_validator("owner_namespace", mode="before")
    @classmethod
    def empty_to_none(cls, v):
        if v is None:
            return None
        if isinstance(v, str) and (v.strip() == ""):
            return None
        return v


class Metadata(schema.Strict):
    api_url: str
    result: basic.JSON
    upload_date: datetime.datetime
    web_url: str | None
