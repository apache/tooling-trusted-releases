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
from typing import Annotated

import pydantic

import atr.models.safe as safe
import atr.models.schema as schema
import atr.models.sql as sql


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and (not value.strip()):
        return None
    return value


def _optional_date(value: object) -> object:
    if isinstance(value, str):
        if not value.strip():
            return None
        return datetime.datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    return value


def _parse_bool(value: object) -> object:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return value


def _required_date(value: object) -> object:
    if isinstance(value, str):
        return datetime.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    return value


_Name = Annotated[str | None, pydantic.BeforeValidator(_blank_to_none)]
_OptionalCommitteeKey = Annotated[safe.CommitteeKey | None, pydantic.BeforeValidator(_blank_to_none)]
_OptionalRelPath = Annotated[safe.RelPath | None, pydantic.BeforeValidator(_blank_to_none)]
_Managed = Annotated[bool, pydantic.BeforeValidator(_parse_bool)]
_RequiredDate = Annotated[datetime.datetime, pydantic.BeforeValidator(_required_date)]
_OptionalDate = Annotated[datetime.datetime | None, pydantic.BeforeValidator(_optional_date)]


class ProjectRow(schema.Subset):
    key: safe.ProjectKey
    name: _Name = None
    status: sql.ProjectStatus
    committee_key: _OptionalCommitteeKey = None
    version_method: sql.VersionMethod

    def to_sql(self) -> sql.Project:
        return sql.Project(
            key=str(self.key),
            name=self.name,
            status=self.status,
            committee_key=str(self.committee_key) if self.committee_key else None,
            version_method=self.version_method,
        )


class ReleaseRow(schema.Subset):
    key: safe.ReleaseKey
    project_key: safe.ProjectKey
    version: safe.VersionKey
    phase: sql.ReleasePhase
    created: _RequiredDate
    released: _OptionalDate = None
    archived: _OptionalDate = None
    release_status: _Name = None

    @property
    def is_archived(self) -> bool:
        return self.release_status == "retired"

    def to_sql(self) -> sql.Release:
        return sql.Release(
            key=str(self.key),
            project_key=str(self.project_key),
            version=str(self.version),
            phase=self.phase,
            created=self.created,
            released=self.released,
            archived=self.archived,
            is_archived=self.is_archived,
        )


class ArtifactRow(schema.Subset):
    project_key: safe.ProjectKey
    version: safe.VersionKey
    artifact_path: safe.RelPath
    signature_path: _OptionalRelPath = None
    checksum_path: _OptionalRelPath = None
    sbom_path: _OptionalRelPath = None
    classification: _Name = None
    download_path_suffix: _OptionalRelPath = None
    managed: _Managed = False
    dated: _OptionalDate = None

    @property
    def release_key(self) -> str:
        return f"{self.project_key}-{self.version}"

    def to_sql(self) -> sql.Artifact:
        return sql.Artifact(
            project_key=str(self.project_key),
            version=str(self.version),
            artifact_path=str(self.artifact_path),
            release_key=self.release_key,
            signature_path=str(self.signature_path) if self.signature_path else None,
            checksum_path=str(self.checksum_path) if self.checksum_path else None,
            sbom_path=str(self.sbom_path) if self.sbom_path else None,
            classification=self.classification,
            download_path_suffix=str(self.download_path_suffix) if self.download_path_suffix else None,
            managed=self.managed,
            dated=self.dated,
        )


type Row = ProjectRow | ReleaseRow | ArtifactRow
