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

import re

import atr.attestable as attestable
import atr.db as db
import atr.models.attestable
import atr.models.github as github
import atr.models.safe as safe
import atr.models.sql as sql


def advance(
    source: atr.models.attestable.SourceV2,
    payload: github.TrustedPublisherPayload | None,
    override: db.Opt[str | None] = db.NOT_SET,
) -> atr.models.attestable.SourceV2:
    source = source.model_copy()
    if (payload is not None) and ((not source.override) or (source.repository == payload.repository)):
        source.repository = payload.repository
        source.default = payload.sha
    if not isinstance(override, db.NotSet):
        if override and (not source.repository):
            raise ValueError("Configure a GitHub repository for this project before setting its source commit.")
        source.override = override
    return source


async def current(release: sql.Release) -> atr.models.attestable.SourceV2:
    previous = None
    if release.latest_revision_number is not None:
        previous = await attestable.load(
            release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
        )
    return await initial(release, previous)


async def initial(
    release: sql.Release, previous: atr.models.attestable.Attestable | None
) -> atr.models.attestable.SourceV2:
    if isinstance(previous, atr.models.attestable.AttestableV2) and (previous.source is not None):
        source = previous.source.model_copy()
        if not source.repository:
            source.repository = repository(release.project)
        return source
    source = atr.models.attestable.SourceV2(repository=repository(release.project))
    if not release.commit_hash:
        return source
    payload = await attestable.latest_github_tp_payload(release.safe_project_key, release.safe_version_key)
    if payload is not None:
        source.repository = payload.repository
        source.default = payload.sha
    if release.commit_hash != source.default:
        source.override = release.commit_hash
    return source


async def inputs(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber
) -> dict[str, str]:
    source = await read(project_key, version_key, revision_number)
    return {"source_repository": source.repository, "source_commit": source.sha}


async def read(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber
) -> atr.models.attestable.SourceV2:
    recorded = await attestable.load(project_key, version_key, revision_number)
    if isinstance(recorded, atr.models.attestable.AttestableV2) and (recorded.source is not None):
        return recorded.source
    payload = await attestable.github_tp_payload_read(project_key, version_key, revision_number)
    if payload is not None:
        return atr.models.attestable.SourceV2(repository=payload.repository, default=payload.sha)
    return atr.models.attestable.SourceV2()


def repository(project: sql.Project) -> str:
    if name := project.policy_github_repository_name:
        return "apache/" + name
    repositories = {
        match[1].removesuffix(".git")
        for url in project.repositories
        if (match := re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/?", url))
    }
    return repositories.pop() if (len(repositories) == 1) else ""
