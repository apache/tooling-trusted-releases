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

import asyncio
import datetime
import hashlib
import itertools
import json
import math
import pathlib
import time
import urllib.parse
import xml.etree.ElementTree as ElementTree
from typing import Any, Final, Literal

import aiohttp
import cyclonedx.exception
import defusedxml.common
import packageurl

import atr.log as log
import atr.metadata as metadata
import atr.models.results as results
import atr.sbom.utilities as utilities
import atr.util as util

ANALYSIS_VERSION: Final = 1
_DEADLINE_SECONDS: Final = 600
_DEPSDEV: Final = "https://api.deps.dev/v3alpha"
_ECOSYSTEMS: Final = "https://packages.ecosyste.ms/api/v1"
_SUPPORTED: Final = frozenset({"cargo", "gem", "golang", "maven", "npm", "nuget", "pypi"})
_TIMEOUT: Final = aiohttp.ClientTimeout(total=60, connect=10)
_USER_AGENT: Final = f"ATR/{metadata.version} (https://releases.apache.org; mailto:dev@tooling.apache.org)"


async def analyse(sboms: dict[str, str]) -> results.SBOMHeatmap:
    started = time.monotonic()
    now = datetime.datetime.now(datetime.UTC)
    async with asyncio.timeout(_DEADLINE_SECONDS):
        snapshots, rows = await _sboms(sboms)
        async with util.create_secure_session(timeout=_TIMEOUT, public=True) as session:
            packages = await _packages(session, sorted({row.key for row in rows}))
            versions = await _versions(session, sorted({row.purl for row in rows if _supported(row)}))
            mirrors = await _mirrors(session, packages)
            for row in rows:
                _observations(row, packages.get(row.key, {}), versions, mirrors)
            projects = await _projects(session, sorted({row.source_repo for row in rows if row.source_repo}))
        for row in rows:
            _scorecard(row, projects.get(row.source_repo or "", {}))
            _score(row, now)
    log.info(
        f"Heatmap analysed {len(snapshots)} SBOMs and {len(rows)} package versions in {time.monotonic() - started:.1f}s"
    )
    return results.SBOMHeatmap(
        analysis_version=ANALYSIS_VERSION,
        generated_at=now.isoformat(),
        sboms=snapshots,
        rows=sorted(rows, key=_order),
        sources={
            "Alpha-Omega heuristic": "https://github.com/alpha-omega-security/heatmap/tree/4ed37981",
            "ecosyste.ms packages": _ECOSYSTEMS,
            "ecosyste.ms repositories": "https://repos.ecosyste.ms/api/v1",
            "ecosyste.ms issues": "https://issues.ecosyste.ms/api/v1",
            "deps.dev and OpenSSF Scorecard": _DEPSDEV,
            "ecosyste.ms data licence": "https://creativecommons.org/licenses/by-sa/4.0/",
            "deps.dev terms": "https://docs.deps.dev/api/v3alpha/#terms",
        },
        attribution=(
            "Alpha-Omega maintenance heuristic, with missing inputs left unknown. "
            "Includes ecosyste.ms data under CC BY-SA 4.0 and deps.dev and OpenSSF Scorecard observations. "
            "ATR selected, normalised and combined records and calculated maintenance indicators. "
            "gitbox_mirror identifies an inferred Apache GitHub mirror, not verified repository identity."
        ),
    )


def _advisories(row: results.SBOMHeatmapRow, versions: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    if not _supported(row):
        return {}
    version = versions.get(row.purl)
    if version is None:
        row.advisory_status = "failed"
        return {}
    if not version:
        row.advisory_status = "not_found"
        return {}
    row.published_at = version.get("publishedAt")
    row.advisories = sorted({advisory["id"] for advisory in version.get("advisoryKeys", [])})
    row.advisory_status = "found" if row.advisories else "none"
    return version


def _components(document: dict[str, Any]) -> list[dict[str, Any]]:
    pending = [document]
    components = []
    while pending:
        component = pending.pop()
        if (not isinstance(component, dict)) or (not isinstance(component.get("components", []), list)):
            raise ValueError("Invalid CycloneDX components")
        components.append(component)
        pending.extend(component.get("components", []))
    return components[1:]


def _gitbox(url: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != "gitbox.apache.org":
        return None
    if parsed.path.startswith("/repos/asf/"):
        name = parsed.path.removeprefix("/repos/asf/")
    elif parsed.path in ("/repos/asf", "/repos/asf.git"):
        names = urllib.parse.parse_qs(parsed.query).get("p", [])
        name = names[0] if (len(names) == 1) else ""
    else:
        return None
    name = name.removesuffix(".git")
    if (not name) or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in name):
        return None
    return f"github.com/apache/{name.lower()}"


def _health(row: results.SBOMHeatmapRow, now: datetime.datetime) -> tuple[float | None, int]:
    released = _timestamp(row.latest_release_at)
    recency = None if (released is None) else 1 - ((now - released).total_seconds() / (365 * 86400 * 3))
    parts = [
        recency,
        row.dds,
        None if (row.active_maintainers is None) else row.active_maintainers / 3,
        None if (row.governance_files is None) else row.governance_files / 3,
    ]
    observed = [max(0, min(1, part)) for part in parts if (part is not None)]
    if row.archived is True:
        return 0.05, len(observed)
    return (sum(observed) / 4 if (len(observed) == 4) else None), len(observed)


def _issues(data: Any) -> dict[str, Any]:
    if (not isinstance(data, dict)) or (not data):
        return {}
    maintainers = data.get("active_maintainers")
    return {"active_maintainers": len(maintainers) if isinstance(maintainers, list) else None}


async def _mirrors(
    session: aiohttp.ClientSession, packages: dict[str, dict[str, Any]]
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    ids = {_gitbox(package.get("repository_url") or "") for package in packages.values()}
    mirrors = {}
    for project_id in sorted(project_id for project_id in ids if project_id):
        name = urllib.parse.quote(project_id.removeprefix("github.com/"), safe="")
        repository = _repository(
            await _request(session, f"https://repos.ecosyste.ms/api/v1/hosts/GitHub/repositories/{name}")
        )
        issues = _issues(await _request(session, f"https://issues.ecosyste.ms/api/v1/hosts/GitHub/repositories/{name}"))
        mirrors[project_id] = (repository, issues)
    return mirrors


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or (not isinstance(value, int | float)) or (not math.isfinite(value)):
        return None
    return float(value)


def _observations(
    row: results.SBOMHeatmapRow,
    package: dict[str, Any],
    versions: dict[str, dict[str, Any] | None],
    mirrors: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    version = _advisories(row, versions)
    row.latest_release_at = package.get("latest_release_published_at")
    row.rankings_average = package.get("rankings_average")
    row.repository_url = package.get("repository_url")
    repository = package.get("repo_metadata") or {}
    issues = package.get("issue_metadata") or {}
    sources = {
        related["projectKey"]["id"]
        for related in version.get("relatedProjects", [])
        if (related.get("relationType") == "SOURCE_REPO") and related.get("projectKey", {}).get("id")
    }
    if len(sources) == 1:
        row.source_repo = _repository_id("https://" + next(iter(sources)))
        row.repository_source = "deps.dev" if row.source_repo else None
    mirror = _gitbox(row.repository_url or "")
    if mirror and ((not sources) or (sources == {mirror})):
        mirror_repository, mirror_issues = mirrors.get(mirror, ({}, {}))
        repository = repository or mirror_repository
        issues = issues or mirror_issues
        if not sources:
            row.source_repo = mirror
            row.repository_source = "gitbox_mirror"
    if (not sources) and (row.source_repo is None):
        row.source_repo = _repository_id(row.repository_url or "")
        row.repository_source = "ecosyste.ms" if row.source_repo else None
    _repository_observations(row, repository, issues)


def _order(row: results.SBOMHeatmapRow) -> tuple[bool, bool, float, str, str]:
    return not row.advisories, row.risk is None, -(row.risk or 0), row.key, row.purl


async def _packages(session: aiohttp.ClientSession, keys: list[str]) -> dict[str, dict[str, Any]]:
    packages = {}
    for batch in itertools.batched(keys, 100):
        records = await _request(session, f"{_ECOSYSTEMS}/packages/bulk_lookup", {"purls": list(batch)})
        if not isinstance(records, list):
            raise ValueError("ecosyste.ms package lookup failed")
        for package in records:
            purl = _purl(package.get("purl"))
            if purl is not None:
                key = str(packageurl.PackageURL(purl.type, purl.namespace, purl.name))
                packages[key] = {
                    "latest_release_published_at": package.get("latest_release_published_at"),
                    "rankings_average": _number((package.get("rankings") or {}).get("average")),
                    "repository_url": package.get("repository_url"),
                    "repo_metadata": _repository(package.get("repo_metadata")),
                    "issue_metadata": _issues(package.get("issue_metadata")),
                }
            del package
        del records
    return packages


def _parse(content: bytes, url: str) -> dict[str, Any]:
    text = content.decode("utf-8-sig")
    path = pathlib.Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path))
    if path.suffix.lower() == ".xml":
        return utilities.text_to_bundle(text, path.with_suffix(".xml")).doc
    document = json.loads(text)
    if (not isinstance(document, dict)) or (document.get("bomFormat") != "CycloneDX"):
        raise ValueError("Unsupported SBOM format; expected CycloneDX JSON or XML")
    return document


async def _projects(session: aiohttp.ClientSession, ids: list[str]) -> dict[str, dict[str, Any]]:
    projects = {}
    for batch in itertools.batched(ids, 5000):
        requests = {"requests": [{"projectKey": {"id": project_id}} for project_id in batch]}
        for response in await _responses(session, "projectbatch", requests) or []:
            if "project" in response:
                projects[response["request"]["projectKey"]["id"]] = response["project"]
    return projects


def _purl(text: Any) -> packageurl.PackageURL | None:
    if not isinstance(text, str):
        return None
    try:
        return packageurl.PackageURL.from_string(text) if text else None
    except ValueError:
        return None


def _repository(data: Any) -> dict[str, Any]:
    if (not isinstance(data, dict)) or (not data):
        return {}
    archived = data.get("archived")
    files = (data.get("metadata") or {}).get("files")
    names = ("security", "code_of_conduct", "contributing")
    governance = None
    if isinstance(files, dict) and all(name in files for name in names):
        governance = sum(bool(files[name]) for name in names)
    return {
        "archived": archived if isinstance(archived, bool) else None,
        "dds": _number((data.get("commit_stats") or {}).get("dds")),
        "governance_files": governance,
    }


def _repository_id(url: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in {"http", "https"}) or (
        parsed.hostname not in {"github.com", "gitlab.com", "bitbucket.org"}
    ):
        return None
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if (len(parts) != 2) or (not all(parts)) or parsed.username or parsed.password:
        return None
    return f"{parsed.hostname}/{'/'.join(parts)}".lower()


def _repository_observations(row: results.SBOMHeatmapRow, repository: dict[str, Any], issues: dict[str, Any]) -> None:
    row.archived = repository.get("archived")
    row.dds = repository.get("dds")
    row.active_maintainers = issues.get("active_maintainers")
    row.governance_files = repository.get("governance_files")


async def _request(session: aiohttp.ClientSession, url: str, body: dict[str, Any] | None = None) -> Any:
    try:
        async with session.request(
            "POST" if (body is not None) else "GET",
            url,
            json=body,
            headers={"User-Agent": _USER_AGENT},
            allow_redirects=False,
        ) as response:
            response.raise_for_status()
            if response.status != 200:
                return None
            return await response.json()
    except (TimeoutError, aiohttp.ClientError, ValueError) as error:
        log.warning(f"Heatmap lookup unavailable at {url}: {error}")
        return None


async def _responses(
    session: aiohttp.ClientSession, endpoint: str, requests: dict[str, Any]
) -> list[dict[str, Any]] | None:
    responses = []
    page_token = ""
    while True:
        body = requests | ({"pageToken": page_token} if page_token else {})
        page = await _request(session, f"{_DEPSDEV}/{endpoint}", body)
        if (not isinstance(page, dict)) or (not isinstance(page.get("responses"), list)):
            return None
        responses.extend(page["responses"])
        page_token = page.get("nextPageToken") or ""
        if not page_token:
            return responses


def _rows(document: dict[str, Any], snapshot: results.SBOMHeatmapSbom) -> list[results.SBOMHeatmapRow]:
    components = _components(document)
    snapshot.components = len(components)
    snapshot.files = sum(component.get("type") == "file" for component in components)
    rows = []
    for component in components:
        if component.get("type") == "file":
            continue
        purl = _purl(component.get("purl"))
        if purl is None:
            continue
        status: Literal["not_found", "version_unknown", "unsupported"] = (
            "not_found" if purl.version else "version_unknown"
        )
        if purl.type not in _SUPPORTED:
            status = "unsupported"
        rows.append(
            results.SBOMHeatmapRow(
                key=str(packageurl.PackageURL(purl.type, purl.namespace, purl.name)),
                purl=_version_purl(purl),
                source_purls=[str(purl)],
                name=purl.name,
                ecosystem=purl.type,
                version=purl.version,
                artifacts=[snapshot.artifact_path],
                advisory_status=status,
            )
        )
    snapshot.packages = len({row.purl for row in rows})
    return rows


async def _sbom(artifact_path: str, url: str) -> tuple[results.SBOMHeatmapSbom, list[results.SBOMHeatmapRow]]:
    snapshot = results.SBOMHeatmapSbom(artifact_path=artifact_path, sbom_url=url)
    try:
        content = await utilities.fetch(url)
        if content is None:
            raise ValueError("SBOM could not be retrieved within the download limits")
        snapshot.sha256 = hashlib.sha256(content).hexdigest()
        snapshot.retrieved_at = datetime.datetime.now(datetime.UTC).isoformat()
        document = await asyncio.to_thread(_parse, content, url)
        return snapshot, _rows(document, snapshot)
    except (
        TimeoutError,
        aiohttp.ClientError,
        ValueError,
        ElementTree.ParseError,
        cyclonedx.exception.CycloneDxException,
        defusedxml.common.DefusedXmlException,
    ) as e:
        snapshot.error = str(e)
        return snapshot, []


async def _sboms(sboms: dict[str, str]) -> tuple[list[results.SBOMHeatmapSbom], list[results.SBOMHeatmapRow]]:
    by_url: dict[str, list[str]] = {}
    for artifact_path, url in sorted(sboms.items()):
        by_url.setdefault(url, []).append(artifact_path)
    snapshots = []
    rows: dict[str, results.SBOMHeatmapRow] = {}
    for url, artifacts in by_url.items():
        snapshot, entries = await _sbom(artifacts[0], url)
        snapshots.extend(snapshot.model_copy(update={"artifact_path": artifact}) for artifact in artifacts)
        for row in entries:
            row.artifacts = artifacts.copy()
            existing = rows.get(row.purl)
            if existing is None:
                rows[row.purl] = row
                continue
            existing.artifacts = sorted(set(existing.artifacts + row.artifacts))
            existing.source_purls = sorted(set(existing.source_purls + row.source_purls))
    if snapshots and all(snapshot.sha256 is None for snapshot in snapshots):
        raise ValueError("No SBOM could be retrieved")
    return snapshots, list(rows.values())


def _score(row: results.SBOMHeatmapRow, now: datetime.datetime) -> None:
    ranking = row.rankings_average
    if (ranking is not None) and (0 <= ranking <= 100):
        row.criticality = 1 - (math.log10(ranking + 1) / math.log10(101))
    row.health, row.health_inputs = _health(row, now)
    if (row.criticality is not None) and (row.health is not None):
        row.risk = row.criticality * (1 - row.health)


def _scorecard(row: results.SBOMHeatmapRow, project: dict[str, Any]) -> None:
    scorecard = project.get("scorecard") or {}
    row.scorecard_date = scorecard.get("date")
    check = next((check for check in scorecard.get("checks", []) if (check.get("name") == "Maintained")), {})
    score = check.get("score")
    row.maintained = score if isinstance(score, int) and (0 <= score <= 10) else None


def _supported(row: results.SBOMHeatmapRow) -> bool:
    return (row.version is not None) and (row.ecosystem in _SUPPORTED)


def _timestamp(value: str | None) -> datetime.datetime | None:
    if value is None:
        return None
    try:
        timestamp = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo else None


def _version_purl(purl: packageurl.PackageURL) -> str:
    return str(packageurl.PackageURL(purl.type, purl.namespace, purl.name, purl.version))


async def _versions(session: aiohttp.ClientSession, purls: list[str]) -> dict[str, dict[str, Any] | None]:
    versions: dict[str, dict[str, Any] | None] = {}
    for batch in itertools.batched(purls, 5000):
        requests = {"requests": [{"purl": purl} for purl in batch]}
        responses = await _responses(session, "purlbatch", requests)
        versions.update(dict.fromkeys(batch, {} if (responses is not None) else None))
        for response in responses or []:
            versions[response["request"]["purl"]] = response.get("result", {}).get("version", {})
    return versions
