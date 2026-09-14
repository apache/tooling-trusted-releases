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

import itertools
import urllib.parse
from typing import Any, Final

import aiohttp

import atr.metadata as metadata
import atr.sbom.maintenance as maintenance
import atr.sbom.streaming as streaming
import atr.util as util

_PACKAGES: Final = "https://packages.ecosyste.ms/api/v1/packages/bulk_lookup"
_TEXT_FIELDS: Final = ("latest_release_published_at", "repository_url")
_TIMEOUT: Final = aiohttp.ClientTimeout(total=60, connect=10)
_USER_AGENT: Final = f"ATR/{metadata.version} (https://releases.apache.org; mailto:dev@tooling.apache.org)"


async def collect(keys: list[str]) -> dict[str, dict[str, Any]]:
    if not keys:
        return {}
    async with util.create_secure_session(timeout=_TIMEOUT, public=True) as session:
        packages = await _packages(session, keys)
        mirrors = await _mirrors(session, packages)
    return {key: _normalise(package, mirrors) for key, package in packages.items()}


async def _mirrors(
    session: aiohttp.ClientSession, packages: dict[str, dict[str, Any]]
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    ids = {
        mirror
        for package in packages.values()
        if ((not package.get("repo_metadata")) or (not package.get("issue_metadata")))
        and (mirror := maintenance.gitbox_mirror(package.get("repository_url") or ""))
    }
    mirrors = {}
    for mirror in sorted(ids):
        name = urllib.parse.quote(mirror.removeprefix("github.com/"), safe="")
        repository = await _request(
            session, f"https://repos.ecosyste.ms/api/v1/hosts/GitHub/repositories/{name}", streaming.REPOSITORY_FIELDS
        )
        issues = await _request(
            session, f"https://issues.ecosyste.ms/api/v1/hosts/GitHub/repositories/{name}", streaming.ISSUE_FIELDS
        )
        mirrors[mirror] = (repository, issues)
    return mirrors


def _normalise(package: dict[str, Any], mirrors: dict[str, tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    repository = package.get("repo_metadata") or {}
    issues = package.get("issue_metadata") or {}
    mirror = maintenance.gitbox_mirror(package.get("repository_url") or "")
    inferred_mirror = None
    if (mirror is not None) and (mirror in mirrors) and ((not repository) or (not issues)):
        mirror_repository, mirror_issues = mirrors[mirror]
        repository = repository or mirror_repository
        issues = issues or mirror_issues
        inferred_mirror = mirror
    return {
        "repository_url": package.get("repository_url"),
        "inferred_mirror": inferred_mirror,
        "latest_release_at": package.get("latest_release_published_at"),
        "rankings_average": maintenance.number((package.get("rankings") or {}).get("average")),
        **maintenance.repository(repository),
        **maintenance.issues(issues),
    }


async def _packages(session: aiohttp.ClientSession, keys: list[str]) -> dict[str, dict[str, Any]]:
    packages = {}
    for batch in itertools.batched(keys, 100):
        async with session.post(
            _PACKAGES, json={"purls": list(batch)}, headers={"User-Agent": _USER_AGENT}, allow_redirects=False
        ) as response:
            _status(response)
            records = await streaming.project_records(response.content, streaming.PACKAGE_FIELDS)
        for record in records:
            key = maintenance.package_key(record.get("purl", ""))
            if (key is None) or (key not in batch) or (key in packages):
                raise ValueError("Unexpected or duplicate package in ecosyste.ms response")
            if not all(isinstance(record.get(field), str | None) for field in _TEXT_FIELDS):
                raise ValueError("Unexpected ecosyste.ms package field type")
            packages[key] = record
    return packages


async def _request(session: aiohttp.ClientSession, url: str, fields: frozenset[tuple[str, ...]]) -> dict[str, Any]:
    async with session.get(url, headers={"User-Agent": _USER_AGENT}, allow_redirects=False) as response:
        if response.status == 404:
            return {}
        _status(response)
        return await streaming.project(response.content, fields)


def _status(response: aiohttp.ClientResponse) -> None:
    response.raise_for_status()
    if response.status != 200:
        raise ValueError(f"Unexpected ecosyste.ms HTTP status {response.status}")
