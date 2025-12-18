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

import json
from typing import TYPE_CHECKING

import asfquart.base as base
import cmarkgfm
import markupsafe

import atr.blueprints.get as get
import atr.db as db
import atr.form as form
import atr.htm as htm
import atr.models.results as results
import atr.models.sql as sql
import atr.sbom as sbom
import atr.sbom.models.osv as osv
import atr.shared as shared
import atr.template as template
import atr.web as web

if TYPE_CHECKING:
    import collections.abc


@get.committer("/sbom/report/<project>/<version>/<path:file_path>")
async def report(session: web.Committer, project: str, version: str, file_path: str) -> str:
    await session.check_access(project)

    # If the draft is not found, we try to get the release candidate
    try:
        release = await session.release(project, version, with_committee=True)
    except base.ASFQuartException:
        release = await session.release(project, version, phase=sql.ReleasePhase.RELEASE_CANDIDATE, with_committee=True)
    is_release_candidate = release.phase == sql.ReleasePhase.RELEASE_CANDIDATE

    async with db.session() as data:
        via = sql.validate_instrumented_attribute
        # TODO: Abstract this code and the sbomtool.MissingAdapter validators
        tasks = (
            await data.task(
                project_name=project,
                version_name=version,
                revision_number=release.latest_revision_number,
                task_type=sql.TaskType.SBOM_TOOL_SCORE,
                primary_rel_path=file_path,
            )
            .order_by(sql.sqlmodel.desc(via(sql.Task.completed)))
            .all()
        )
        # Run or running scans for the current revision
        osv_tasks = (
            await data.task(
                project_name=project,
                version_name=version,
                task_type=sql.TaskType.SBOM_OSV_SCAN,
                primary_rel_path=file_path,
                revision_number=release.latest_revision_number,
            )
            .order_by(sql.sqlmodel.desc(via(sql.Task.added)))
            .all()
        )

    block = htm.Block()
    block.h1["SBOM report"]

    await _report_task_results(block, list(tasks))

    task_result = tasks[0].result
    if not isinstance(task_result, results.SBOMToolScore):
        raise base.ASFQuartException("Invalid SBOM score result", errorcode=500)

    _report_header(block, is_release_candidate, release, task_result)

    if not is_release_candidate:
        _augment_section(block, release, task_result)

    _conformance_section(block, task_result)

    block.h2["Vulnerabilities"]

    if task_result.vulnerabilities is not None:
        vulnerabilities = [
            sbom.models.osv.CdxVulnAdapter.validate_python(json.loads(e)) for e in task_result.vulnerabilities
        ]
    else:
        vulnerabilities = []

    _vulnerability_scan_section(
        block, project, version, file_path, task_result, vulnerabilities, osv_tasks, is_release_candidate
    )

    block.h2["Outdated tool"]
    outdated = None
    if task_result.outdated:
        outdated = sbom.models.maven.OutdatedAdapter.validate_python(json.loads(task_result.outdated))
    if outdated:
        if outdated.kind == "tool":
            block.p[
                f"""The CycloneDX Maven Plugin is outdated. The used version is
                {outdated.used_version} and the available version is
                {outdated.available_version}."""
            ]
        else:
            block.p[
                f"""There was a problem with the SBOM detected when trying to
                determine if the CycloneDX Maven Plugin is outdated:
                {outdated.kind.upper()}."""
            ]
    else:
        block.p["No outdated tool found."]

    _cyclonedx_cli_errors(block, task_result)

    return await template.blank("SBOM report", content=block.collect())


def _conformance_section(block: htm.Block, task_result: results.SBOMToolScore) -> None:
    warnings = [sbom.models.conformance.MissingAdapter.validate_python(json.loads(w)) for w in task_result.warnings]
    errors = [sbom.models.conformance.MissingAdapter.validate_python(json.loads(e)) for e in task_result.errors]
    if warnings:
        block.h2["Warnings"]
        _missing_table(block, warnings)

    if errors:
        block.h2["Errors"]
        _missing_table(block, errors)

    if not (warnings or errors):
        block.h2["Conformance report"]
        block.p["No NTIA 2021 minimum data field conformance warnings or errors found."]


def _report_header(
    block: htm.Block, is_release_candidate: bool, release: sql.Release, task_result: results.SBOMToolScore
) -> None:
    block.p[
        """This is a report by the ATR SBOM tool, for debugging and
        informational purposes. Please use it only as an approximate
        guideline to the quality of your SBOM file."""
    ]
    if not is_release_candidate:
        block.p[
            "This report is for revision ", htm.code[task_result.revision_number], "."
        ]  # TODO: Mark if a subsequent score has failed
    elif release.phase == sql.ReleasePhase.RELEASE_CANDIDATE:
        block.p[f"This report is for the latest {release.version} release candidate."]


async def _report_task_results(block: htm.Block, tasks: list[sql.Task]):
    if not tasks:
        block.p["No SBOM score found."]
        return await template.blank("SBOM report", content=block.collect())

    task_status = tasks[0].status
    task_error = tasks[0].error
    if task_status == sql.TaskStatus.QUEUED:
        block.p["SBOM score is being computed."]
        return await template.blank("SBOM report", content=block.collect())

    if task_status == sql.TaskStatus.FAILED:
        block.p[f"SBOM score task failed: {task_error}"]
        return await template.blank("SBOM report", content=block.collect())


def _augment_section(block: htm.Block, release: sql.Release, task_result: results.SBOMToolScore):
    # TODO: Show the status if the task to augment the SBOM is still running
    # And then don't allow it to be augmented again
    augments = []
    if task_result.atr_props is not None:
        augments = [t.get("value", "") for t in task_result.atr_props if t.get("name", "") == "asf:atr:augment"]
    if len(augments) == 0:
        block.p["We can attempt to augment this SBOM with additional data."]
        form.render_block(
            block,
            model_cls=shared.sbom.AugmentSBOMForm,
            submit_label="Augment SBOM",
            empty=True,
        )
    else:
        if release.latest_revision_number in augments:
            block.p["This SBOM was augmented by ATR."]
        else:
            block.p["This SBOM was augmented by ATR at revision ", htm.code[augments[-1]], "."]
            block.p["We can perform augmentation again to check for additional new data."]
            form.render_block(
                block,
                model_cls=shared.sbom.AugmentSBOMForm,
                submit_label="Re-augment SBOM",
                empty=True,
            )


def _cyclonedx_cli_errors(block: htm.Block, task_result: results.SBOMToolScore):
    block.h2["CycloneDX CLI validation errors"]
    if task_result.cli_errors:
        block.pre["\n".join(task_result.cli_errors)]
    else:
        block.p["No CycloneDX CLI validation errors found."]


def _extract_vulnerability_severity(vuln: osv.VulnerabilityDetails) -> str:
    """Extract severity information from vulnerability data."""
    data = vuln.database_specific or {}
    if "severity" in data:
        return data["severity"]

    severity_data = vuln.severity
    if severity_data and isinstance(severity_data, list):
        first_severity = severity_data[0]
        if isinstance(first_severity, dict) and ("type" in first_severity):
            return first_severity["type"]

    return "Unknown"


def _missing_table(block: htm.Block, items: list[sbom.models.conformance.Missing]) -> None:
    warning_rows = [
        htm.tr[
            htm.td[
                kind.upper()
                if (len(components) == 0)
                else htm.details[htm.summary[kind.upper()], htm.div[_detail_table(components)]]
            ],
            htm.td[prop],
            htm.td[str(count)],
        ]
        for kind, prop, count, components in _missing_tally(items)
    ]
    block.table(".table.table-sm.table-bordered.table-striped")[
        htm.thead[htm.tr[htm.th["Kind"], htm.th["Property"], htm.th["Count"]]],
        htm.tbody[*warning_rows],
    ]


def _detail_table(components: list[str | None]):
    return htm.table(".table.table-sm.table-bordered.table-striped")[
        htm.tbody[[htm.tr[htm.td[comp.capitalize()]] for comp in components if comp is not None]],
    ]


def _missing_tally(items: list[sbom.models.conformance.Missing]) -> list[tuple[str, str, int, list[str | None]]]:
    counts: dict[tuple[str, str], int] = {}
    components: dict[tuple[str, str], list[str | None]] = {}
    for item in items:
        key = (getattr(item, "kind", ""), getattr(getattr(item, "property", None), "name", ""))
        counts[key] = counts.get(key, 0) + 1
        if key not in components:
            components[key] = [str(item)]
        elif item.kind == "missing_component_property":
            components[key].append(str(item))
    return sorted(
        [(item, prop, count, components.get((item, prop), [])) for (item, prop), count in counts.items()],
        key=lambda kv: (kv[0], kv[1]),
    )


def _vulnerability_component_details_osv(block: htm.Block, component: results.OSVComponent) -> None:
    details_content = []
    summary_element = htm.summary[
        htm.span(".badge.bg-danger.me-2.font-monospace")[str(len(component.vulnerabilities))],
        htm.strong[component.purl],
    ]
    details_content.append(summary_element)

    for vuln in component.vulnerabilities:
        vuln_id = vuln.id or "Unknown"
        vuln_summary = vuln.summary
        vuln_refs = []
        if vuln.references is not None:
            vuln_refs = [r for r in vuln.references if r.get("type", "") == "WEB"]
        vuln_primary_ref = vuln_refs[0] if (len(vuln_refs) > 0) else {}
        vuln_modified = vuln.modified or "Unknown"
        vuln_severity = _extract_vulnerability_severity(vuln)

        vuln_header = [htm.a(href=vuln_primary_ref.get("url", ""), target="_blank")[htm.strong(".me-2")[vuln_id]]]
        if vuln_severity != "Unknown":
            vuln_header.append(htm.span(".badge.bg-warning.text-dark")[vuln_severity])

        details = markupsafe.Markup(cmarkgfm.github_flavored_markdown_to_html(vuln.details))
        vuln_div = htm.div(".ms-3.mb-3.border-start.border-warning.border-3.ps-3")[
            htm.div(".d-flex.align-items-center.mb-2")[*vuln_header],
            htm.p(".mb-1")[vuln_summary],
            htm.div(".text-muted.small")[
                "Last modified: ",
                vuln_modified,
            ],
            htm.div(".mt-2.text-muted")[details or "No additional details available."],
        ]
        details_content.append(vuln_div)

    block.append(htm.details(".mb-3.rounded")[*details_content])


def _vulnerability_scan_button(block: htm.Block) -> None:
    block.p["No new vulnerability scan has been performed for this revision."]

    form.render_block(
        block,
        model_cls=shared.sbom.ScanSBOMForm,
        submit_label="Scan file",
        empty=True,
    )


def _vulnerability_scan_find_completed_task(
    osv_tasks: collections.abc.Sequence[sql.Task], revision_number: str
) -> sql.Task | None:
    """Find the most recent completed OSV scan task for the given revision."""
    for task in osv_tasks:
        if task.status == sql.TaskStatus.COMPLETED and (task.result is not None):
            task_result = task.result
            if isinstance(task_result, results.SBOMOSVScan) and task_result.revision_number == revision_number:
                return task
    return None


def _vulnerability_scan_find_in_progress_task(
    osv_tasks: collections.abc.Sequence[sql.Task], revision_number: str
) -> sql.Task | None:
    """Find the most recent in-progress OSV scan task for the given revision."""
    for task in osv_tasks:
        if task.revision_number == revision_number:
            if task.status in (sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE, sql.TaskStatus.FAILED):
                return task
    return None


def _vulnerability_scan_results(
    block: htm.Block, vulns: list[osv.CdxVulnerabilityDetail], scans: list[str], task: sql.Task | None
) -> None:
    if task is not None:
        task_result = task.result
        if not isinstance(task_result, results.SBOMOSVScan):
            block.p["Invalid scan result format."]
            return

        components = task_result.components
        ignored = task_result.ignored
        ignored_count = len(ignored)

        if not components:
            block.p["No vulnerabilities found."]
            if ignored_count > 0:
                component_word = "component was" if (ignored_count == 1) else "components were"
                block.p[f"{ignored_count} {component_word} ignored due to missing PURL or version information:"]
                block.p[f"{','.join(ignored)}"]
            return

        block.p[f"Scan found vulnerabilities in {len(components)} components:"]

        for component in components:
            _vulnerability_component_details_osv(block, component)

        if ignored_count > 0:
            component_word = "component was" if (ignored_count == 1) else "components were"
            block.p[f"{ignored_count} {component_word} ignored due to missing PURL or version information:"]
            block.p[f"{','.join(ignored)}"]
    else:
        if len(vulns) == 0:
            block.p["No vulnerabilities listed in this SBOM."]
            return
        components = {a.get("ref", "") for v in vulns if v.affects is not None for a in v.affects}

        if len(scans) > 0:
            block.p["This SBOM was scanned for vulnerabilities at revision ", htm.code[scans[-1]], "."]

        block.p[f"Vulnerabilities found in {len(components)} components:"]

        for component in components:
            _vulnerability_component_details_osv(
                block,
                results.OSVComponent(
                    purl=component,
                    vulnerabilities=[
                        _cdx_to_osv(v)
                        for v in vulns
                        if v.affects is not None and component in [a.get("ref") for a in v.affects]
                    ],
                ),
            )


def _cdx_to_osv(cdx: osv.CdxVulnerabilityDetail) -> osv.VulnerabilityDetails:
    score = []
    severity = ""
    if cdx.ratings is not None:
        severity, score = sbom.utilities.cdx_severity_to_osv(cdx.ratings)
    return osv.VulnerabilityDetails(
        id=cdx.id,
        summary=cdx.description,
        details=cdx.detail,
        modified=cdx.updated or "",
        published=cdx.published,
        severity=score,
        database_specific={"severity": severity},
        references=[{"type": "WEB", "url": a.get("url", "")} for a in cdx.advisories]
        if cdx.advisories is not None
        else [],
    )


def _vulnerability_scan_section(
    block: htm.Block,
    project: str,
    version: str,
    file_path: str,
    task_result: results.SBOMToolScore,
    vulnerabilities: list[osv.CdxVulnerabilityDetail],
    osv_tasks: collections.abc.Sequence[sql.Task],
    is_release_candidate: bool,
) -> None:
    """Display the vulnerability scan section based on task status."""
    completed_task = _vulnerability_scan_find_completed_task(osv_tasks, task_result.revision_number)

    in_progress_task = _vulnerability_scan_find_in_progress_task(osv_tasks, task_result.revision_number)

    scans = []
    if task_result.atr_props is not None:
        scans = [t.get("value", "") for t in task_result.atr_props if t.get("name", "") == "asf:atr:osv-scan"]
    _vulnerability_scan_results(block, vulnerabilities, scans, completed_task)

    if not is_release_candidate:
        if in_progress_task is not None:
            _vulnerability_scan_status(block, in_progress_task, project, version, file_path)
        else:
            _vulnerability_scan_button(block)


def _vulnerability_scan_status(block: htm.Block, task: sql.Task, project: str, version: str, file_path: str) -> None:
    status_text = task.status.value.replace("_", " ").capitalize()
    block.p[f"Vulnerability scan is currently {status_text.lower()}."]
    block.p["Task ID: ", htm.code[str(task.id)]]
    if (task.status == sql.TaskStatus.FAILED) and (task.error is not None):
        block.p[
            "Task reported an error: ",
            htm.code[task.error],
            ". Additional details are unavailable from ATR.",
        ]
        _vulnerability_scan_button(block)
