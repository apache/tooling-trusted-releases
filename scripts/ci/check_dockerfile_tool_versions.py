#!/usr/bin/env python3

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

"""Notice when the external tools pinned in Dockerfile.alpine have newer upstream releases.

This is notify only. It never fails the build; it prints a report of the pinned tools
that are behind their latest upstream version, so a scheduled workflow can raise or
refresh a tracking issue. Someone can then test the newer version before bumping it.

The GitHub-hosted tools are resolved from their latest release. Apache RAT is resolved
from ATR's own release catalogue, which already tracks Apache versions, rather than from
GitHub.
"""

import asyncio
import os
import pathlib
import re
import sys
from typing import Final, NamedTuple

import aiohttp

_DOCKERFILE: Final = pathlib.Path("Dockerfile.alpine")
_GITHUB_API: Final = "https://api.github.com"

# The tools resolved from a GitHub repository's latest release, keyed by the
# Dockerfile ENV that pins them, with the binary name each produces.
_GITHUB_TOOLS: Final[dict[str, tuple[str, str]]] = {
    "SYFT_VERSION": ("anchore/syft", "syft"),
    "PARLAY_VERSION": ("snyk/parlay", "parlay"),
    "SBOMQS_VERSION": ("interlynk-io/sbomqs", "sbomqs"),
    "CDXCLI_VERSION": ("CycloneDX/cyclonedx-cli", "cyclonedx-cli"),
}

# Apache RAT lives in ATR's catalogue feed under this project key.
_RAT_PROJECT_KEY: Final = "creadur-rat"
_RAT_VERSION_VAR: Final = "RAT_VERSION"
_RELEASE_CATALOG_URL: Final = os.environ.get("RELEASE_CATALOG_URL", "https://release-catalog.apache.org/")
_REQUEST_TIMEOUT: Final = aiohttp.ClientTimeout(total=30)


class _Result(NamedTuple):
    tool: str
    current: str
    latest: str | None


def main() -> None:
    asyncio.run(_run())


def _emit_output(behind: list[_Result]) -> None:
    # Hand the scheduled workflow a single flag for whether to raise an issue.
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path is None:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"stale={'true' if behind else 'false'}\n")


def _github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _github_latest(session: aiohttp.ClientSession, repo: str) -> str | None:
    url = f"{_GITHUB_API}/repos/{repo}/releases/latest"
    try:
        async with session.get(url, headers=_github_headers()) as response:
            if response.status != 200:
                return None
            data = await response.json()
    except aiohttp.ClientError:
        return None
    tag = data.get("tag_name")
    return tag.lstrip("v") if isinstance(tag, str) else None


async def _github_result(session: aiohttp.ClientSession, var: str, current: str | None) -> _Result:
    repo, label = _GITHUB_TOOLS[var]
    latest = None if current is None else await _github_latest(session, repo)
    return _Result(tool=label, current=current or "unknown", latest=latest)


def _is_behind(current: str, latest: str) -> bool:
    current_parsed = _parse_version(current)
    latest_parsed = _parse_version(latest)
    if (current_parsed is None) or (latest_parsed is None):
        return current != latest
    return latest_parsed > current_parsed


def _parse_version(text: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in text.strip().lstrip("v").split("."))
    except ValueError:
        return None


def _pinned_versions() -> dict[str, str]:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    pinned: dict[str, str] = {}
    for match in re.finditer(r'(?m)^ENV\s+([A-Z0-9_]+_VERSION)=("?)([^"\s]+)\2', text):
        pinned[match.group(1)] = match.group(3)
    return pinned


async def _rat_latest(session: aiohttp.ClientSession) -> str | None:
    url = _RELEASE_CATALOG_URL.rstrip("/") + "/project_releases.json"
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)
    except aiohttp.ClientError:
        return None
    entry = data.get(_RAT_PROJECT_KEY) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return None
    candidates: list[tuple[tuple[int, ...], str]] = []
    for release in entry.get("release", []):
        revision = release.get("revision")
        if isinstance(revision, str):
            parsed = _parse_version(revision)
            if parsed is not None:
                candidates.append((parsed, revision))
    return max(candidates)[1] if candidates else None


async def _rat_result(session: aiohttp.ClientSession, current: str | None) -> _Result:
    latest = None if current is None else await _rat_latest(session)
    return _Result(tool="apache-rat", current=current or "unknown", latest=latest)


def _report(behind: list[_Result], unknown: list[_Result]) -> None:
    if behind:
        print("## Dockerfile tool updates available\n")
        print("These tools pinned in `Dockerfile.alpine` have newer upstream releases. Test the")
        print("newer version before bumping it; this notice changes nothing on its own.\n")
        print("| Tool | Pinned | Latest |")
        print("| --- | --- | --- |")
        for result in behind:
            print(f"| {result.tool} | {result.current} | {result.latest} |")
        print()
    else:
        print("## Dockerfile tools are up to date\n")
        print("Every pinned tool matches its latest upstream release.\n")
    if unknown:
        names = ", ".join(sorted(result.tool for result in unknown))
        print(f"Could not determine the latest version for: {names}.")


async def _run() -> None:
    pinned = _pinned_versions()
    async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
        github = [_github_result(session, var, pinned.get(var)) for var in _GITHUB_TOOLS]
        results = list(await asyncio.gather(*github, _rat_result(session, pinned.get(_RAT_VERSION_VAR))))
    behind = [result for result in results if (result.latest is not None) and _is_behind(result.current, result.latest)]
    unknown = [result for result in results if result.latest is None]
    _report(behind, unknown)
    _emit_output(behind)


if __name__ == "__main__":
    sys.exit(main())
