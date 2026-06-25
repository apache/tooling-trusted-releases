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

import os
from typing import TYPE_CHECKING, Any, Final

import aiohttp
from packageurl import PackageURL

import atr.util as util

from . import models
from .utilities import get_pointer, osv_severity_to_cdx

if TYPE_CHECKING:
    from cyclonedx.model.component import Component

_DEBUG: bool = os.environ.get("DEBUG_SBOM_TOOL") == "1"
_OSV_API_BASE: Final[str] = "https://api.osv.dev/v1"
_API_TIMEOUT: Final = aiohttp.ClientTimeout(total=60, connect=10)
_SOURCE_DATABASE_NAMES: Final = {
    "ASB": "Android Security Bulletin",
    "PUB": "Android Security Bulletin",
    "ALSA": "AlmaLinux Security Advisory",
    "ALBA": "AlmaLinux Security Advisory",
    "ALEA": "AlmaLinux Security Advisory",
    "ALPINE": "Alpine Security Advisory",
    "BELL": "BellSoft Security Advisory",
    "BIT": "Bitnami Vulnerability Database",
    "CGA": "Chainguard Security Notices",
    "CURL": "Curl CVEs",
    "CVE": "National Vulnerability Database",
    "DEBIAN": "Debian Security Tracker",
    "DSA": "Debian Security Advisory",
    "DLA": "Debian Security Advisory",
    "DTSA": "Debian Security Advisory",
    "ECHO": "Echo Security Advisory",
    "EEF": "Erlang Ecosystem Foundation CNA Vulnerabilities",
    "ELA": "Debian Extended LTS Security Advisory",
    "GHSA": "GitHub Security Advisory",
    "GO": "Go Vulnerability Database",
    "GSD": "Global Security Database",
    "HSEC": "Haskell Security Advisory",
    "JLSEC": "Julia Security Advisory",
    "KUBE": "Kubernetes Official CVE Feed",
    "LBSEC": "LoopBack Advisory Database",
    "LSN": "Livepatch Security Notices",
    "MGASA": "Mageia Security Advisory",
    "MAL": "Malicious Packages Repository",
    "MINI": "Minimus Security Notices",
    "OESA": "openEuler Security Advisory",
    "OSV": "OSV Advisory",
    "PHSA": "VMWare Photon Security Advisory",
    "PSF": "Python Software Foundation Vulnerability Database",
    "PYSEC": "PyPI Vulnerability Database",
    "RHSA": "Red Hat Security Data",
    "RHBA": "Red Hat Security Data",
    "RHEA": "Red Hat Security Data",
    "RLSA": "Rocky Linux Security Advisory",
    "RXSA": "Rocky Linux Security Advisory",
    "RSEC": "RConsortium Advisory Database",
    "RUSTSEC": "RustSec Advisory Database",
    "SUSE": "SUSE Security Landing Page",
    "openSUSE": "SUSE Security Landing Page",
    "UBUNTU": "Ubuntu CVE Reports",
    "USN": "Ubuntu Security Notices",
    "V8": "V8/Chromium Time-Based Policy",
}


async def scan_bundle(bundle: models.bundle.Bundle) -> tuple[list[models.osv.ComponentVulnerabilities], list[str]]:
    components = list(bundle.bom.components)
    queries, ignored = _scan_bundle_build_queries(components)
    if _DEBUG:
        print(f"[DEBUG] Scanning {len(queries)} components for vulnerabilities")
        ignored_count = len(ignored)
        if ignored_count > 0:
            print(f"[DEBUG] {ignored_count} components ignored (missing purl or version)")
    # Reusable HTTP session we can use for all fetches
    async with util.create_secure_session(timeout=_API_TIMEOUT) as session:
        component_vulns_map = await _scan_bundle_fetch_vulnerabilities(session, queries, 1000)
        if _DEBUG:
            print(f"[DEBUG] Total components with vulnerabilities: {len(component_vulns_map)}")
        await _scan_bundle_populate_vulnerabilities(session, component_vulns_map)
    result: list[models.osv.ComponentVulnerabilities] = []
    for ref, vulns in component_vulns_map.items():
        result.append(models.osv.ComponentVulnerabilities(ref=ref, vulnerabilities=vulns))
    return result, ignored


async def vuln_patch(
    doc: dict[str, Any],
    components: list[models.osv.ComponentVulnerabilities],
) -> models.patch.Patch:
    patch_ops: models.patch.Patch = []
    _assemble_vulnerabilities(doc, patch_ops)
    ix = 0
    for c in components:
        for vuln in c.vulnerabilities:
            _assemble_component_vulnerability(doc, patch_ops, c.ref, vuln, ix)
            ix += 1
    return patch_ops


def vulns_from_bundle(bundle: models.bundle.Bundle) -> list[models.osv.CdxVulnerabilityDetail]:
    vulns = get_pointer(bundle.doc, "/vulnerabilities")
    if vulns is None:
        return []
    print(f"[DEBUG] {len(vulns)} vulnerabilities:\n{vulns}")
    return [models.osv.CdxVulnerabilityDetail.model_validate(v) for v in vulns]


def _assemble_component_vulnerability(
    doc: dict[str, Any], patch_ops: models.patch.Patch, ref: str, vuln: models.osv.VulnerabilityDetails, index: int
) -> None:
    vulnerability = {
        "bom-ref": f"vuln:{ref}/{vuln.id}",
        "id": vuln.id,
        "source": _get_source(vuln),
        "description": vuln.summary,
        "detail": vuln.details,
        "cwes": [int(r.replace("CWE-", "")) for r in vuln.database_specific.get("cwe_ids", [])],
        "published": vuln.published,
        "updated": vuln.modified,
        "affects": [{"ref": ref}],
        "ratings": osv_severity_to_cdx(vuln.severity, vuln.database_specific.get("severity", "")),
    }
    if vuln.references is not None:
        vulnerability["advisories"] = [
            {"url": r["url"]}
            for r in vuln.references
            if ((r.get("type", "") == "WEB") and ("advisories" in r.get("url", "")))
            or (r.get("type", "") == "ADVISORY")
        ]
    patch_ops.append(
        models.patch.AddOp(
            op="add",
            path=f"/vulnerabilities/{index!s}",
            value=vulnerability,
        )
    )


def _assemble_vulnerabilities(doc: dict[str, Any], patch_ops: models.patch.Patch) -> None:
    if get_pointer(doc, "/vulnerabilities") is not None:
        patch_ops.append(models.patch.RemoveOp(op="remove", path="/vulnerabilities"))
    patch_ops.append(
        models.patch.AddOp(
            op="add",
            path="/vulnerabilities",
            value=[],
        )
    )


def _component_purl_with_version(component: Component) -> str | None:
    # If we don't know the purl, we can't help
    if component.purl is None:
        return None
    # If the component purl includes version information, return it
    if component.purl.version is not None:
        return str(component.purl)
    # If not, and we don't have a component version, we still can't add anything
    if component.version is None:
        return None
    version = component.version.strip()
    if (not version) or version == "UNKNOWN":
        return None
    # Make a new purl so we don't affect the component definition
    purl = PackageURL.from_string(str(component.purl))
    return str(PackageURL(purl.type, purl.namespace, purl.name, version, purl.qualifiers, purl.subpath))


async def _fetch_vulnerabilities_for_batch(
    session: aiohttp.ClientSession,
    queries: list[dict[str, Any]],
) -> list[models.osv.QueryResult]:
    if _DEBUG:
        print(f"[DEBUG] Sending querybatch with {len(queries)} queries")
    payload = {"queries": queries}
    async with session.post(f"{_OSV_API_BASE}/querybatch", json=payload) as response:
        # TODO: Should we retry?
        response.raise_for_status()
        data = await response.json()
    results_data = data.get("results", [])
    if _DEBUG:
        print(f"[DEBUG] Received {len(results_data)} results")
    return [models.osv.QueryResult.model_validate(result) for result in results_data]


async def _fetch_vulnerability_details(
    session: aiohttp.ClientSession,
    vuln_id: str,
) -> models.osv.VulnerabilityDetails:
    if _DEBUG:
        print(f"[DEBUG] Fetching details for {vuln_id}")
    async with session.get(f"{_OSV_API_BASE}/vulns/{vuln_id}") as response:
        response.raise_for_status()
        data = await response.json()
        return models.osv.VulnerabilityDetails.model_validate(data)


def _get_source(vuln: models.osv.VulnerabilityDetails) -> dict[str, str]:
    db = vuln.id.split("-")[0]
    web_refs = list(filter(lambda v: v.get("type", "") == "WEB", vuln.references)) if vuln.references else []
    first_ref = web_refs[0] if (len(web_refs) > 0) else None

    name = _SOURCE_DATABASE_NAMES.get(db, "Unknown Database")
    source = {"name": name}
    if first_ref is not None:
        source["url"] = first_ref.get("url", "")
    return source


async def _paginate_query(
    session: aiohttp.ClientSession,
    query: dict[str, Any],
    page_token: str,
) -> list[models.osv.VulnerabilityDetails]:
    all_vulns: list[models.osv.VulnerabilityDetails] = []
    current_query = query.copy()
    current_query["page_token"] = page_token
    page = 0
    while True:
        page += 1
        if _DEBUG and (page > 1):
            print(f"[DEBUG] Paginating query (page {page})")
        results = await _fetch_vulnerabilities_for_batch(session, [current_query])
        if not results:
            break
        result = results[0]
        if result.vulns:
            all_vulns.extend(result.vulns)
        next_page_token = result.next_page_token
        if next_page_token is None:
            break
        current_query["page_token"] = next_page_token
    return all_vulns


def _scan_bundle_build_queries(
    components: list[Component],
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    queries: list[tuple[str, dict[str, Any]]] = []
    ignored = []
    for component in components:
        purl_with_version = _component_purl_with_version(component)
        if purl_with_version is None:
            ignored.append(component.name)
            continue
        query = {"package": {"purl": purl_with_version}}
        if component.bom_ref:
            queries.append((str(component.bom_ref), query))
    return queries, ignored


async def _scan_bundle_fetch_vulnerabilities(
    session: aiohttp.ClientSession,
    queries: list[tuple[str, dict[str, Any]]],
    batch_size: int,
) -> dict[str, list[models.osv.VulnerabilityDetails]]:
    component_vulns_map: dict[str, list[models.osv.VulnerabilityDetails]] = {}
    for batch_start in range(0, len(queries), batch_size):
        batch_end = min(batch_start + batch_size, len(queries))
        batch = queries[batch_start:batch_end]
        if _DEBUG:
            batch_num = batch_start // batch_size + 1
            print(f"[DEBUG] Processing batch {batch_num} ({batch_start + 1}-{batch_end}/{len(queries)})")
        batch_queries = [query for _ref, query in batch]
        batch_results = await _fetch_vulnerabilities_for_batch(session, batch_queries)
        if _DEBUG and (len(batch_results) != len(batch)):
            print(f"[DEBUG] count mismatch (expected {len(batch)}, got {len(batch_results)})")
        for i, (ref, query) in enumerate(batch):
            if i >= len(batch_results):
                break
            query_result = batch_results[i]
            if query_result.vulns:
                existing_vulns = component_vulns_map.setdefault(ref, [])
                existing_vulns.extend(query_result.vulns)
                if _DEBUG:
                    print(f"[DEBUG] {ref}: {len(query_result.vulns)} vulnerabilities")
            if query_result.next_page_token:
                if _DEBUG:
                    print(f"[DEBUG] {ref}: has pagination, fetching remaining pages")
                existing_vulns = component_vulns_map.setdefault(ref, [])
                paginated = await _paginate_query(session, query, query_result.next_page_token)
                existing_vulns.extend(paginated)
    return component_vulns_map


async def _scan_bundle_populate_vulnerabilities(
    session: aiohttp.ClientSession,
    component_vulns_map: dict[str, list[models.osv.VulnerabilityDetails]],
) -> None:
    details_cache: dict[str, models.osv.VulnerabilityDetails] = {}
    for vulns in component_vulns_map.values():
        for i, vuln in enumerate(vulns):
            vuln_id = vuln.id
            if not vuln_id:
                continue
            details = details_cache.get(vuln_id)
            if details is None:
                details = await _fetch_vulnerability_details(session, vuln_id)
                details_cache[vuln_id] = details
            vulns[i] = details
    if _DEBUG:
        print(f"[DEBUG] Fetched details for {len(details_cache)} unique vulnerabilities")
