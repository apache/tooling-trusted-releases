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
from typing import TYPE_CHECKING, Any, Literal

import asfquart.base as base
import cmarkgfm
import markupsafe

import atr.blueprints.get as get
import atr.db as db
import atr.form as form
import atr.get.compose as compose
import atr.get.vote as vote
import atr.htm as htm
import atr.models.results as results
import atr.models.sql as sql
import atr.sbom as sbom
import atr.sbom.models.osv as osv
import atr.shared as shared
import atr.template as template
import atr.util as util
import atr.web as web

if TYPE_CHECKING:
    from collections.abc import Sequence


@get.committer("/sbom/report/<project>/<version>/<path:file_path>")
async def report(session: web.Committer, project: str, version: str, file_path: str) -> str:
    await session.check_access(project)

    # If the draft is not found, we try to get the release candidate
    try:
        release = await session.release(project, version, with_committee=True)
    except base.ASFQuartException:
        release = await session.release(project, version, phase=sql.ReleasePhase.RELEASE_CANDIDATE, with_committee=True)

    block = htm.Block()

    is_release_candidate = False
    back_url = ""
    back_anchor = ""
    phase: Literal["COMPOSE", "VOTE"] = "COMPOSE"
    match release.phase:
        case sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            back_url = util.as_url(compose.selected, project_name=release.project.name, version_name=release.version)
            back_anchor = f"Compose {release.project.short_display_name} {release.version}"
            phase = "COMPOSE"
        case sql.ReleasePhase.RELEASE_CANDIDATE:
            is_release_candidate = True
            back_url = util.as_url(vote.selected, project_name=release.project.name, version_name=release.version)
            back_anchor = f"Vote on {release.project.short_display_name} {release.version}"
            phase = "VOTE"

    shared.distribution.html_nav(
        block,
        back_url=back_url,
        back_anchor=back_anchor,
        phase=phase,
    )

    block.h1["SBOM report"]

    validated_path = form.to_relpath(file_path)
    if validated_path is None:
        raise base.ASFQuartException("Invalid file path", errorcode=400)
    validated_path_str = str(validated_path)

    task, augment_tasks, osv_tasks = await _fetch_tasks(validated_path_str, project, release, version)

    task_status = await _report_task_results(block, task)
    if task_status:
        return task_status

    if task is None or (not isinstance(task.result, results.SBOMToolScore)):
        raise base.ASFQuartException("Invalid SBOM score result", errorcode=500)

    task_result = task.result
    _report_header(block, is_release_candidate, release, task_result)

    if not is_release_candidate:
        latest_augment = None
        last_augmented_bom = None
        if len(augment_tasks) > 0:
            latest_augment = augment_tasks[0]
            augment_results: list[Any] = [t.result for t in augment_tasks]
            augmented_bom_versions = [
                r.bom_version for r in augment_results if (r is not None) and (r.bom_version is not None)
            ]
            if len(augmented_bom_versions) > 0:
                last_augmented_bom = max(augmented_bom_versions)
        _augment_section(block, release, task_result, latest_augment, last_augmented_bom)

    _conformance_section(block, task_result)
    _license_section(block, task_result)

    _vulnerability_scan_section(block, project, version, file_path, task_result, osv_tasks, is_release_candidate)

    _outdated_tool_section(block, task_result)

    _cyclonedx_cli_errors(block, task_result)

    return await template.blank("SBOM report", content=block.collect())


async def _fetch_tasks(
    file_path: str, project: str, release: sql.Release, version: str
) -> tuple[sql.Task | None, Sequence[sql.Task], Sequence[sql.Task]]:
    # TODO: Abstract this code and the sbomtool.MissingAdapter validators
    async with db.session() as data:
        via = sql.validate_instrumented_attribute
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
        augment_tasks = (
            await data.task(
                project_name=project,
                version_name=version,
                task_type=sql.TaskType.SBOM_AUGMENT,
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
        return tasks[0] if len(tasks) > 0 else None, augment_tasks, osv_tasks


def _outdated_tool_section(block: htm.Block, task_result: results.SBOMToolScore):
    block.h2["Outdated tools"]
    if task_result.outdated:
        outdated = []
        if isinstance(task_result.outdated, str):
            # Older version, only checked one tool
            outdated = [sbom.models.tool.OutdatedAdapter.validate_python(json.loads(task_result.outdated))]
        elif isinstance(task_result.outdated, list):
            # Newer version, checked multiple tools
            outdated = [sbom.models.tool.OutdatedAdapter.validate_python(json.loads(o)) for o in task_result.outdated]
        if len(outdated) == 0:
            block.p["No outdated tools found."]
        for result in outdated:
            if result.kind == "tool":
                if "Apache Trusted Releases" in result.name:
                    block.p[
                        f"""The last version of ATR used on this SBOM was
                            {result.used_version} but ATR is currently version
                            {result.available_version}."""
                    ]
                else:
                    block.p[
                        f"""The {result.name} is outdated. The used version is
                            {result.used_version} and the available version is
                            {result.available_version}."""
                    ]
            else:
                if (result.kind == "missing_metadata") or (result.kind == "missing_timestamp"):
                    # These both return without checking any further tools as they prevent checking
                    block.p[
                        f"""There was a problem with the SBOM detected when trying to
                            determine if any tools were outdated:
                            {result.kind.upper()}."""
                    ]
                else:
                    block.p[
                        f"""There was a problem with the SBOM detected when trying to
                            determine if the {result.name} is outdated:
                            {result.kind.upper()}."""
                    ]
    else:
        block.p["No outdated tools found."]


def _conformance_section(block: htm.Block, task_result: results.SBOMToolScore) -> None:
    block.h2["Conformance report"]
    warnings = [sbom.models.conformance.MissingAdapter.validate_python(json.loads(w)) for w in task_result.warnings]
    errors = [sbom.models.conformance.MissingAdapter.validate_python(json.loads(e)) for e in task_result.errors]
    if warnings:
        block.h3[htm.icon("exclamation-triangle-fill", ".me-2.text-warning"), "Warnings"]
        _missing_table(block, warnings)

    if errors:
        block.h3[htm.icon("x-octagon-fill", ".me-2.text-danger"), "Errors"]
        _missing_table(block, errors)

    if not (warnings or errors):
        block.p["No NTIA 2021 minimum data field conformance warnings or errors found."]


def _license_section(block: htm.Block, task_result: results.SBOMToolScore) -> None:
    block.h2["Licenses"]
    warnings = []
    errors = []
    prev_licenses = None
    if task_result.prev_licenses is not None:
        prev_licenses = _load_license_issues(task_result.prev_licenses)
    if task_result.license_warnings is not None:
        warnings = _load_license_issues(task_result.license_warnings)
    if task_result.license_errors is not None:
        errors = _load_license_issues(task_result.license_errors)
    # TODO: Rework the rendering of these since category in the table is redundant.
    if warnings:
        block.h3[htm.icon("exclamation-triangle-fill", ".me-2.text-warning"), "Warnings"]
        _license_table(block, warnings, prev_licenses)

    if errors:
        block.h3[htm.icon("x-octagon-fill", ".me-2.text-danger"), "Errors"]
        _license_table(block, errors, prev_licenses)

    if not (warnings or errors):
        block.p["No license warnings or errors found."]


def _load_license_issues(issues: list[str]) -> list[sbom.models.licenses.Issue]:
    return [sbom.models.licenses.Issue.model_validate(json.loads(i)) for i in issues]


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


async def _report_task_results(block: htm.Block, task: sql.Task | None):
    if task is None:
        block.p["No SBOM score found."]
        return await template.blank("SBOM report", content=block.collect())

    task_status = task.status
    task_error = task.error
    if task_status == sql.TaskStatus.QUEUED or task_status == sql.TaskStatus.ACTIVE:
        block.p["SBOM score is being computed."]
        return await template.blank("SBOM report", content=block.collect())

    if task_status == sql.TaskStatus.FAILED:
        block.p[f"SBOM score task failed: {task_error}"]
        return await template.blank("SBOM report", content=block.collect())


def _augment_section(
    block: htm.Block,
    release: sql.Release,
    task_result: results.SBOMToolScore,
    latest_task: sql.Task | None,
    last_bom: int | None,
):
    augments = []
    if task_result.atr_props is not None:
        augments = [t.get("value", "") for t in task_result.atr_props if t.get("name", "") == "asf:atr:augment"]
    if latest_task is not None:
        result: Any = latest_task.result
        if (latest_task.status == sql.TaskStatus.ACTIVE) or (latest_task.status == sql.TaskStatus.QUEUED):
            block.p["This SBOM is currently being augmented by ATR."]
            return
        if latest_task.status == sql.TaskStatus.FAILED:
            block.p[f"ATR attempted to augment this SBOM but failed: {latest_task.error}"]
            return
        if (last_bom is not None) and (result.bom_version == last_bom) and (len(augments) != 0):
            block.p["This SBOM was augmented by ATR at revision ", htm.code[augments[-1]], "."]
            return

    if len(augments) == 0:
        block.p["We can attempt to augment this SBOM with additional data."]
        form.render_block(
            block,
            model_cls=shared.sbom.AugmentSBOMForm,
            submit_label="Augment SBOM",
            empty=True,
        )
    else:
        # These are edge cases as they cover situations where the BOM says it was augmented but we don't have a task
        # record for it
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


def _license_table(
    block: htm.Block,
    items: list[sbom.models.licenses.Issue],
    prev: list[sbom.models.licenses.Issue] | None,
) -> None:
    warning_rows = [
        htm.tr[
            htm.td[
                f"Category {category!s}"
                if (len(components) == 0)
                else htm.details[htm.summary[f"Category {category!s}"], htm.div[_detail_table(components)]]
            ],
            htm.td[f"{count!s} {f'({new!s} new, {updated!s} changed)' if new or updated else ''}"],
        ]
        for category, count, new, updated, components in _license_tally(items, prev)
    ]
    block.table(".table.table-sm.table-bordered.table-striped")[
        htm.thead[htm.tr[htm.th["License Category"], htm.th["Count"]]],
        htm.tbody[*warning_rows],
    ]


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
        htm.tbody[[htm.tr[htm.td[comp]] for comp in components if comp is not None]],
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


# TODO: Update this to return either a block or something we can use later in a block for styling reasons
def _license_tally(
    items: list[sbom.models.licenses.Issue],
    old_issues: list[sbom.models.licenses.Issue] | None,
) -> list[tuple[sbom.models.licenses.Category, int, int | None, int | None, list[str | None]]]:
    counts: dict[sbom.models.licenses.Category, int] = {}
    components: dict[sbom.models.licenses.Category, list[str | None]] = {}
    new_count = 0
    updated_count = 0
    old_map = {lic.component_name: (lic.license_expression, lic.category) for lic in old_issues} if old_issues else None
    for item in items:
        key = item.category
        counts[key] = counts.get(key, 0) + 1
        name = str(item).capitalize()
        if old_map is not None:
            if item.component_name not in old_map:
                new_count = new_count + 1
                name = f"{name} (new)"
            elif item.license_expression != old_map[item.component_name][0]:
                updated_count = updated_count + 1
                name = f"{name} (previously {old_map[item.component_name][0]} - Category {
                    str(old_map[item.component_name][1]).upper()
                })"
        if key not in components:
            components[key] = [name]
        else:
            components[key].append(name)
    return sorted(
        [
            (
                category,
                count,
                new_count if old_issues is not None else None,
                updated_count if old_issues is not None else None,
                components.get(category, []),
            )
            for category, count in counts.items()
        ],
        key=lambda kv: kv[0].value,
    )


def _severity_to_style(severity: str) -> str:
    match severity.lower():
        case "critical":
            return ".bg-danger.text-light"
        case "high":
            return ".bg-danger.text-light"
        case "medium":
            return ".bg-warning.text-dark"
        case "moderate":
            return ".bg-warning.text-dark"
        case "low":
            return ".bg-warning.text-dark"
        case "info":
            return ".bg-info.text-light"
    return ".bg-info.text-light"


def _vulnerability_component_details_osv(
    block: htm.Block,
    component: results.OSVComponent,
    previous_vulns: dict[str, tuple[str, list[str]]] | None,  # id: severity
) -> int:
    severities = ["critical", "high", "medium", "moderate", "low", "info", "none", "unknown"]
    new = 0
    worst = 99

    vuln_details = []
    for vuln in component.vulnerabilities:
        is_new = False
        vuln_id = vuln.id or "Unknown"
        vuln_summary = vuln.summary
        vuln_refs = []
        if vuln.references is not None:
            vuln_refs = [r for r in vuln.references if r.get("type", "") == "WEB"]
        vuln_primary_ref = vuln_refs[0] if (len(vuln_refs) > 0) else {}
        vuln_modified = vuln.modified or "Unknown"

        vuln_severity = _extract_vulnerability_severity(vuln)
        worst = _update_worst_severity(severities, vuln_severity, worst)

        if previous_vulns is not None:
            if (
                (vuln_id not in previous_vulns)
                or previous_vulns[vuln_id][0] != vuln_severity
                or (component.purl not in previous_vulns[vuln_id][1])
            ):
                is_new = True
                new = new + 1

        vuln_header = [htm.a(href=vuln_primary_ref.get("url", ""), target="_blank")[htm.strong(".me-2")[vuln_id]]]
        style = f".badge.me-2{_severity_to_style(vuln_severity)}"
        vuln_header.append(htm.span(style)[vuln_severity])

        if (previous_vulns is not None) and is_new:
            if (vuln_id in previous_vulns) and (component.purl in previous_vulns[vuln_id][1]):
                # If it's there, the sev must have changed
                vuln_header.append(htm.icon("arrow-left", ".me-2"))
                vuln_header.append(
                    htm.span(f".badge{_severity_to_style(previous_vulns[vuln_id][0])}.atr-text-strike")[
                        previous_vulns[vuln_id][0]
                    ]
                )
            else:
                vuln_header.append(htm.span(".badge.bg-info.text-light")["new"])

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
        vuln_details.append(vuln_div)

    badge_style = ""
    if worst < len(severities):
        badge_style = _severity_to_style(severities[worst])
    summary_elements = [htm.span(f".badge{badge_style}.me-2.font-monospace")[str(len(component.vulnerabilities))]]
    if new > 0:
        summary_elements.append(htm.span(".badge.me-2.bg-info")[f"{new!s} new"])
    summary_elements.append(htm.strong[component.purl])
    details_content = [htm.summary[*summary_elements], *vuln_details]
    block.append(htm.details(".mb-3.rounded")[*details_content])
    return new


def _update_worst_severity(severities: list[str], vuln_severity: str, worst: int) -> int:
    try:
        sev_index = severities.index(vuln_severity)
    except ValueError:
        sev_index = 99
    worst = min(worst, sev_index)
    return worst


def _vulnerability_scan_button(block: htm.Block) -> None:
    block.p["You can perform a new vulnerability scan."]

    form.render_block(
        block,
        model_cls=shared.sbom.ScanSBOMForm,
        submit_label="Scan file",
        empty=True,
    )


def _vulnerability_scan_find_completed_task(osv_tasks: Sequence[sql.Task], revision_number: str) -> sql.Task | None:
    """Find the most recent completed OSV scan task for the given revision."""
    for task in osv_tasks:
        if (task.status == sql.TaskStatus.COMPLETED) and (task.result is not None):
            task_result = task.result
            if isinstance(task_result, results.SBOMOSVScan) and (task_result.revision_number == revision_number):
                return task
    return None


def _vulnerability_scan_find_in_progress_task(osv_tasks: Sequence[sql.Task], revision_number: str) -> sql.Task | None:
    """Find the most recent in-progress OSV scan task for the given revision."""
    for task in osv_tasks:
        if task.revision_number == revision_number:
            if task.status in (sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE, sql.TaskStatus.FAILED):
                return task
    return None


def _vulnerability_scan_results(
    block: htm.Block,
    vulns: list[osv.CdxVulnerabilityDetail],
    scans: list[str],
    task: sql.Task | None,
    prev: list[osv.CdxVulnerabilityDetail] | None,
) -> None:
    previous_vulns = None
    if prev is not None:
        previous_osv = [
            (_cdx_to_osv(v), [a.get("ref", "") for a in v.affects] if v.affects is not None else []) for v in prev
        ]
        previous_vulns = {v.id: (_extract_vulnerability_severity(v), a) for v, a in previous_osv}
    if task is not None:
        _vulnerability_results_from_scan(task, block, previous_vulns)
    else:
        _vulnerability_results_from_bom(vulns, block, scans, previous_vulns)


def _vulnerability_results_from_bom(
    vulns: list[osv.CdxVulnerabilityDetail],
    block: htm.Block,
    scans: list[str],
    previous_vulns: dict[str, tuple[str, list[str]]] | None,
) -> None:
    total_new = 0
    new_block = htm.Block()
    if len(vulns) == 0:
        block.p["No vulnerabilities listed in this SBOM."]
        return
    components = {a.get("ref", "") for v in vulns if v.affects is not None for a in v.affects}

    if len(scans) > 0:
        block.p["This SBOM was scanned for vulnerabilities at revision ", htm.code[scans[-1]], "."]

    for component in components:
        new = _vulnerability_component_details_osv(
            new_block,
            results.OSVComponent(
                purl=component,
                vulnerabilities=[
                    _cdx_to_osv(v)
                    for v in vulns
                    if (v.affects is not None) and (component in [a.get("ref") for a in v.affects])
                ],
            ),
            previous_vulns,
        )
        total_new = total_new + new

    new_str = f" ({total_new!s} new since last release)" if (total_new > 0) else ""
    block.p[f"Vulnerabilities{new_str} found in {len(components)} components:"]
    block.append(new_block)


def _vulnerability_results_from_scan(
    task: sql.Task, block: htm.Block, previous_vulns: dict[str, tuple[str, list[str]]] | None
) -> None:
    total_new = 0
    new_block = htm.Block()
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

    for component in components:
        new = _vulnerability_component_details_osv(new_block, component, previous_vulns)
        total_new = total_new + new

    new_str = f" ({total_new!s} new since last release)" if (total_new > 0) else ""
    block.p[f"Scan found vulnerabilities{new_str} in {len(components)} components:"]

    if ignored_count > 0:
        component_word = "component was" if (ignored_count == 1) else "components were"
        block.p[f"{ignored_count} {component_word} ignored due to missing PURL or version information:"]
        block.p[f"{','.join(ignored)}"]
    block.append(new_block)


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
        if (cdx.advisories is not None)
        else [],
    )


def _vulnerability_scan_section(
    block: htm.Block,
    project: str,
    version: str,
    file_path: str,
    task_result: results.SBOMToolScore,
    osv_tasks: Sequence[sql.Task],
    is_release_candidate: bool,
) -> None:
    """Display the vulnerability scan section based on task status."""
    completed_task = _vulnerability_scan_find_completed_task(osv_tasks, task_result.revision_number)

    in_progress_task = _vulnerability_scan_find_in_progress_task(osv_tasks, task_result.revision_number)

    block.h2["Vulnerabilities"]

    scans = []
    if task_result.vulnerabilities is not None:
        vulnerabilities = [
            sbom.models.osv.CdxVulnAdapter.validate_python(json.loads(e)) for e in task_result.vulnerabilities
        ]
    else:
        vulnerabilities = []
    if task_result.prev_vulnerabilities is not None:
        prev_vulnerabilities = [
            sbom.models.osv.CdxVulnAdapter.validate_python(json.loads(e)) for e in task_result.prev_vulnerabilities
        ]
    else:
        prev_vulnerabilities = None
    if task_result.atr_props is not None:
        scans = [t.get("value", "") for t in task_result.atr_props if t.get("name", "") == "asf:atr:osv-scan"]
    _vulnerability_scan_results(block, vulnerabilities, scans, completed_task, prev_vulnerabilities)

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
