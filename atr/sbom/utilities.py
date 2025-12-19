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
from typing import TYPE_CHECKING, Any

import cvss

if TYPE_CHECKING:
    import pathlib

    import atr.sbom.models.osv as osv

import aiohttp
import yyjson

from . import constants, models


def get_atr_version():
    try:
        from atr import metadata

        return metadata.version
    except ImportError:
        return "cli"


_ATR_VERSION = get_atr_version()

_SCORING_METHODS_OSV = {"CVSS_V2": "CVSSv2", "CVSS_V3": "CVSSv3", "CVSS_V4": "CVSSv4"}
_SCORING_METHODS_CDX = {"CVSSv2": "CVSS_V2", "CVSSv3": "CVSS_V3", "CVSSv4": "CVSS_V4", "other": "Other"}
_CDX_SEVERITIES = ["critical", "high", "medium", "low", "info", "none", "unknown"]
_TOOL_METADATA = {
    "bom-ref": "tool:asf:atr",
    "provider": {
        "name": constants.conformance.THE_APACHE_SOFTWARE_FOUNDATION,
        "url": ["https://apache.org/"],
    },
    "name": "Apache Trusted Releases",
    "authenticated": True,
    "version": _ATR_VERSION,
}


def apply_patch(
    reason: str, revision: str, bundle: models.bundle.Bundle, patch_ops: models.patch.Patch
) -> tuple[int, yyjson.Document]:
    """Take a list of patch operations and apply them to the bundle. Returns the patched document, with the
    task recorded in `properties` and the SBOM version number incremented as per the CDX spec."""
    _record_task(reason, revision, bundle.doc, patch_ops)
    new_version = _increment_version(bundle.doc, patch_ops)
    patch_data = patch_to_data(patch_ops)
    return new_version, bundle.doc.patch(yyjson.Document(patch_data))


async def bundle_to_ntia_patch(bundle_value: models.bundle.Bundle) -> models.patch.Patch:
    from .conformance import ntia_2021_issues, ntia_2021_patch

    _warnings, errors = ntia_2021_issues(bundle_value.bom)
    async with aiohttp.ClientSession() as session:
        patch_ops = await ntia_2021_patch(session, bundle_value.doc, errors)
    return patch_ops


async def bundle_to_vuln_patch(
    bundle_value: models.bundle.Bundle, vulnerabilities: list[osv.ComponentVulnerabilities]
) -> models.patch.Patch:
    from .osv import vuln_patch

    patch_ops = await vuln_patch(bundle_value.doc, vulnerabilities)
    return patch_ops


def get_pointer(doc: yyjson.Document, path: str) -> Any | None:
    try:
        return doc.get_pointer(path)
    except ValueError as exc:
        # TODO: This is not necessarily stable
        if str(exc) == "JSON pointer cannot be resolved":
            return None
        raise


def get_props_from_bundle(bundle_value: models.bundle.Bundle) -> tuple[int, list[dict[str, str]]]:
    version: int | None = get_pointer(bundle_value.doc, "/version")
    if version is None:
        version = 0
    properties: list[dict[str, str]] | None = get_pointer(bundle_value.doc, "/properties")
    if properties is None:
        return version, []
    return version, [p for p in properties if "asf:atr:" in p.get("name", "")]


def patch_to_data(patch_ops: models.patch.Patch) -> list[dict[str, Any]]:
    return [op.model_dump(by_alias=True, exclude_none=True) for op in patch_ops]


def path_to_bundle(path: pathlib.Path) -> models.bundle.Bundle:
    text = path.read_text(encoding="utf-8")
    bom = models.bom.Bom.model_validate_json(text)
    return models.bundle.Bundle(doc=yyjson.Document(text), bom=bom, path=path, text=text)


def osv_severity_to_cdx(severity: list[dict[str, Any]] | None, textual: str) -> list[dict[str, str | float]] | None:
    if severity is not None:
        return [
            {
                "severity": _map_severity(textual),
                "method": _SCORING_METHODS_OSV.get(s.get("type", ""), "other"),
                **_extract_cdx_score(_SCORING_METHODS_OSV.get(s.get("type", ""), "other"), s.get("score", "")),
            }
            for s in severity
        ]
    return None


def cdx_severity_to_osv(severity: list[dict[str, str | float]]) -> tuple[str | None, list[dict[str, str]]]:
    severities = [
        {
            "score": str(s.get("score", str(s.get("vector", "")))),
            "type": _SCORING_METHODS_CDX.get(str(s.get("method", "other"))),
        }
        for s in severity
    ]
    textual = severity[0].get("severity")
    return str(textual), severities


def _extract_cdx_score(type: str, score_str: str) -> dict[str, str | float]:
    if "CVSS" in score_str or "CVSS" in type:
        components = re.match(r"CVSS:(?P<version>\d+\.?\d*)/.+", score_str)
        parsed = None
        vector = score_str
        if components is None:
            # CVSS2 doesn't include the version in the string, but we know this is a CVSS vector
            parsed = cvss.CVSS2(vector)
        else:
            version = components.group("version")
            if "3" in version or "V3" in type:
                parsed = cvss.CVSS3(vector)
            elif "4" in version or "V4" in type:
                parsed = cvss.CVSS4(vector)
        if parsed is not None:
            # Pull a different score depending on which sections are filled out
            scores = parsed.scores()
            severities = parsed.severities()
            score: float | None = next((s for s in reversed(scores) if s is not None), None)
            severity = next((s for s in reversed(severities) if s is not None), "unknown")
            result: dict[str, str | float] = {"vector": vector, "severity": _map_severity(severity)}
            if score is not None:
                result["score"] = score
            return result
        # Some vector that failed to parse
        return {"vector": score_str}
    else:
        try:
            # Maybe the score is just a numeric score
            return {"score": float(score_str)}
        except ValueError:
            # If not, it must just be a string (eg. Ubuntu scoring system)
            return {"severity": score_str}


def _increment_version(doc: yyjson.Document, patch_ops: models.patch.Patch) -> int:
    version: int | None = get_pointer(doc, "/version")
    if version is not None:
        version = version + 1
        patch_ops.append(models.patch.ReplaceOp(op="replace", path="/version", value=version))
    else:
        # This shouldn't happen, but we can handle it just in case
        version = 1
        patch_ops.append(models.patch.AddOp(op="add", path="/version", value=version))
    return version


def _map_severity(severity: str) -> str:
    sev = severity.lower()
    if sev in _CDX_SEVERITIES:
        return sev
    else:
        # Map known github values
        if sev == "moderate":
            return "medium"
    return "unknown"


def _record_task(task: str, revision: str, doc: yyjson.Document, patch_ops: models.patch.Patch) -> models.patch.Patch:
    properties: list[dict[str, str]] | None = get_pointer(doc, "/properties")
    tools: dict[str, Any] | None = get_pointer(doc, "/metadata/tools")
    services: list[dict[str, Any]] | None = get_pointer(doc, "/metadata/tools/services")
    operation = {"name": f"asf:atr:{task}", "value": revision}
    if properties is None:
        patch_ops.append(models.patch.AddOp(op="add", path="/properties", value=[operation]))
    else:
        properties.append(operation)
        patch_ops.append(models.patch.ReplaceOp(op="replace", path="/properties", value=properties))
    if tools is None:
        tools = {}
        patch_ops.append(models.patch.AddOp(op="add", path="/metadata/tools", value=tools))
    if services is None:
        services = []
        patch_ops.append(models.patch.AddOp(op="add", path="/metadata/tools/services", value=services))
    try:
        tool_index = [s.get("bom-ref", "") for s in services].index("tool:asf:atr")
    except ValueError:
        tool_index = -1
    if tool_index == -1:
        patch_ops.append(
            models.patch.AddOp(op="add", path=f"/metadata/tools/services/{len(services)}", value=_TOOL_METADATA)
        )
    else:
        # If we ever add more metadata to this, such as task-specific properties under the tool, this changes
        patch_ops.append(
            models.patch.ReplaceOp(op="replace", path=f"/metadata/tools/services/{tool_index}", value=_TOOL_METADATA)
        )
    return patch_ops
