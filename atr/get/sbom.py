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

import asyncio
import json
import re
from typing import Any, Final, Literal

import cmarkgfm
import markupsafe

import atr.blueprints.get as get
import atr.db as db
import atr.get.compose as compose
import atr.get.vote as vote
import atr.htm as htm
import atr.log as log
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.render as render
import atr.sbom as sbom
import atr.shared as shared
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def quality(
    session: web.Committer,
    _sbom_quality: Literal["sbom/quality"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_path: safe.RelPath,
) -> str:
    """
    URL: /sbom/quality/<project_key>/<version_key>/<file_path>
    """
    release = await shared.sbom.release_in_phase(session, project_key, version_key, with_committee=True)
    base_path = paths.release_directory(release)

    block = htm.Block()
    _phase_nav(block, release)
    block.h1["SBOM report"]
    block.p["A report on the SBOM ", htm.code[str(file_path)], "."]

    task = await _score_task(str(file_path), project_key, release, version_key)
    task_result = _score_result(task)

    block.h2["Content"]
    breakdown = await _breakdown(base_path, file_path)
    block.h3["Components"]
    if breakdown is None:
        block.p["This SBOM could not be read, so its components cannot be shown."]
    else:
        _components_section(block, breakdown)
    _license_section(block, task)
    _vulnerability_section(block, task, task_result)

    block.h2["Quality"]
    # The quality checks all read from the score task, so until it has run there is nothing to show
    if task_result is None:
        block.p[_score_unavailable(task)]
    else:
        _conformance_section(block, task_result)
        _outdated_tool_section(block, task_result)
        _cyclonedx_cli_errors(block, task_result)

    return await template.blank("SBOM report", content=block.collect())


async def _score_task(
    file_path: str, project: safe.ProjectKey, release: sql.Release, version: safe.VersionKey
) -> sql.Task | None:
    async with db.session() as data:
        via = sql.validate_instrumented_attribute
        tasks = (
            await data.task(
                project_key=str(project),
                version_key=str(version),
                revision_number=release.latest_revision_number,
                task_type=sql.TaskType.SBOM_TOOL_SCORE,
                primary_rel_path=file_path,
            )
            .order_by(sql.sqlmodel.desc(via(sql.Task.completed)))
            .all()
        )
        return tasks[0] if (len(tasks) > 0) else None


def _vulnerability_section(block: htm.Block, task: sql.Task | None, task_result: results.SBOMToolScore | None) -> None:
    block.h3["Vulnerabilities"]
    # These are the vulnerabilities the SBOM itself declares, which the score task reads out of the document
    if task_result is None:
        block.p[_score_unavailable(task)]
        return
    if task_result.vulnerabilities is not None:
        vulnerabilities = [results.CdxVulnAdapter.validate_python(json.loads(e)) for e in task_result.vulnerabilities]
    else:
        vulnerabilities = []
    previous_vulns = None
    if task_result.prev_vulnerabilities is not None:
        prev = [results.CdxVulnAdapter.validate_python(json.loads(e)) for e in task_result.prev_vulnerabilities]
        previous_osv = [
            (_cdx_to_osv(v), [a.get("ref", "") for a in v.affects] if (v.affects is not None) else []) for v in prev
        ]
        previous_vulns = {v.id: (_extract_vulnerability_severity(v), a) for v, a in previous_osv}
    _vulnerability_results_from_bom(vulnerabilities, block, [], previous_vulns)


async def _breakdown(base_path: safe.StatePath, sbom_rel_path: safe.RelPath) -> sbom.models.components.Breakdown | None:
    abs_path = (base_path / sbom_rel_path).path
    try:
        bundle = await asyncio.to_thread(sbom.utilities.path_to_bundle, abs_path)
    except Exception:
        # A malformed SBOM is the author's to fix, so say so on the page rather than erroring out.
        # The parser gives no single error to catch, and anything it throws means the same thing here
        log.exception(f"Could not read SBOM at {sbom_rel_path}")
        return None
    return sbom.components.breakdown(bundle.bom)


def _cdx_to_osv(cdx: results.CdxVulnerabilityDetail) -> results.VulnerabilityDetails:
    score = []
    severity = ""
    if cdx.ratings is not None:
        severity, score = sbom.utilities.cdx_severity_to_osv(cdx.ratings)
    return results.VulnerabilityDetails(
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


def _component_label(item: sbom.models.components.Item) -> str:
    return item.name if (item.version is None) else f"{item.name} {item.version}"


def _components_section(
    block: htm.Block,
    breakdown: sbom.models.components.Breakdown,
) -> None:
    if breakdown.subject is not None:
        block.p["This SBOM describes ", htm.strong[_component_label(breakdown.subject)], "."]

    if breakdown.total == 0:
        block.p["This SBOM does not declare any components."]
        return

    component_word = "component" if (breakdown.total == 1) else "components"
    type_word = "type" if (len(breakdown.groups) == 1) else "types"
    block.p[f"{breakdown.total} {component_word} of {len(breakdown.groups)} {type_word}:"]

    for group in breakdown.groups:
        block.append(
            htm.details(".mb-3.rounded")[
                htm.summary[
                    htm.span(".badge.bg-secondary.me-2.font-monospace")[str(len(group.items))],
                    htm.strong[group.component_type.capitalize()],
                ],
                _components_table(group.items),
            ]
        )


def _components_table(
    items: list[sbom.models.components.Item],
) -> htm.Element:
    rows = [
        htm.tr[
            htm.td[item.name],
            htm.td[item.version or "-"],
            htm.td[_licenses_cell(item.license_choices)],
            htm.td[htm.code[item.purl] if item.purl else "-"],
        ]
        for item in items
    ]
    return htm.table(".table.table-sm.table-bordered.table-striped")[
        htm.thead[htm.tr[htm.th["Name"], htm.th["Version"], htm.th["Licenses"], htm.th["PURL"]]],
        htm.tbody[*rows],
    ]


def _conformance_section(block: htm.Block, task_result: results.SBOMToolScore) -> None:
    block.h3["Conformance report"]
    warnings = [sbom.models.conformance.MissingAdapter.validate_python(json.loads(w)) for w in task_result.warnings]
    errors = [sbom.models.conformance.MissingAdapter.validate_python(json.loads(e)) for e in task_result.errors]
    if warnings:
        block.h4[htm.icon("exclamation-triangle-fill", ".me-2.text-warning"), "Warnings"]
        _missing_table(block, warnings)

    if errors:
        block.h4[htm.icon("x-octagon-fill", ".me-2.text-danger"), "Errors"]
        _missing_table(block, errors)

    if not (warnings or errors):
        block.p["No NTIA 2021 minimum data field conformance warnings or errors found."]


_CVE_ID: Final = re.compile(r"CVE-\d{4}-\d+")


def _cve_reference(references: list[dict[str, Any]]) -> tuple[str, str] | None:
    # OSV keys a vulnerability by its own id, often a GHSA, but records the CVE alias among the
    # references. Where a CVE is present it is the name people search for, so we label and link with it
    for reference in references:
        url = reference.get("url", "")
        match = _CVE_ID.search(url)
        if match is not None:
            return match.group(), url
    return None


def _cyclonedx_cli_errors(block: htm.Block, task_result: results.SBOMToolScore):
    block.h3["CycloneDX CLI validation errors"]
    if task_result.cli_errors:
        block.pre["\n".join(task_result.cli_errors)]
    else:
        block.p["No CycloneDX CLI validation errors found."]


def _detail_table(components: list[str | None]):
    return htm.table(".table.table-sm.table-bordered.table-striped")[
        htm.tbody[[htm.tr[htm.td[comp]] for comp in components if comp is not None]],
    ]


def _extract_vulnerability_severity(vuln: results.VulnerabilityDetails) -> str:
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


def _choice_badges(choice: sbom.models.licenses.Choice) -> list[htm.Element]:
    # A single licence, or an AND that all applies, shows one badge for the whole expression. An OR
    # ATR resolved shows the chosen half in its category colour and the halves it set aside drained
    # of colour, so a reader sees what was on offer without the alternatives reading as a verdict
    if choice.chosen is None:
        return [_license_badge(choice.expression, choice.category)]
    return [
        _license_badge(choice.chosen, choice.category),
        *(_license_alternative_badge(name, category) for name, category in choice.alternatives),
    ]


def _license_alternative_badge(name: str, category: sbom.models.licenses.Category) -> htm.Element:
    # The category box keeps its letter but loses its colour, marking a licence ATR could have used
    # but set aside in favour of a friendlier half of the same OR
    return htm.div(".d-flex.align-items-center.text-muted")[
        htm.span(".badge.me-2.bg-secondary.bg-opacity-25.text-dark")[str(category)],
        name,
    ]


def _license_badge(name: str, category: sbom.models.licenses.Category) -> htm.Element:
    return htm.div(".d-flex.align-items-center")[
        htm.span(f".badge.me-2{_license_category_style(category)}")[str(category)],
        name,
    ]


def _license_category_style(category: sbom.models.licenses.Category) -> str:
    match category:
        case sbom.models.licenses.Category.A:
            return ".bg-success"
        case sbom.models.licenses.Category.B:
            return ".bg-warning.text-dark"
        case sbom.models.licenses.Category.X:
            return ".bg-danger"


def _license_section(block: htm.Block, task: sql.Task | None) -> None:
    block.h3["Licenses"]
    # The licence issues come from the score, so without one there is nothing to list
    task_result = _score_result(task)
    if task_result is None:
        block.p[_score_unavailable(task)]
        return
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
        block.h4[htm.icon("exclamation-triangle-fill", ".me-2.text-warning"), "Warnings"]
        _license_table(block, warnings, prev_licenses)

    if errors:
        block.h4[htm.icon("x-octagon-fill", ".me-2.text-danger"), "Errors"]
        _license_table(block, errors, prev_licenses)

    if not (warnings or errors):
        block.p["No license warnings or errors found."]


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
            htm.td[f"{count!s} {f'({new!s} new, {updated!s} changed)' if (new or updated) else ''}"],
        ]
        for category, count, new, updated, components in _license_tally(items, prev)
    ]
    block.table(".table.table-sm.table-bordered.table-striped")[
        htm.thead[htm.tr[htm.th["License Category"], htm.th["Count"]]],
        htm.tbody[*warning_rows],
    ]


# TODO: Update this to return either a block or something we can use later in a block for styling reasons
def _license_tally(
    items: list[sbom.models.licenses.Issue],
    old_issues: list[sbom.models.licenses.Issue] | None,
) -> list[tuple[sbom.models.licenses.Category, int, int | None, int | None, list[str | None]]]:
    counts: dict[sbom.models.licenses.Category, int] = {}
    components: dict[sbom.models.licenses.Category, list[str | None]] = {}
    new_counts: dict[sbom.models.licenses.Category, int] = {}
    updated_counts: dict[sbom.models.licenses.Category, int] = {}
    old_map = {lic.component_name: (lic.license_expression, lic.category) for lic in old_issues} if old_issues else None
    for item in items:
        key = item.category
        counts[key] = counts.get(key, 0) + 1
        name = str(item).capitalize()
        if old_map is not None:
            if item.component_name not in old_map:
                new_counts[key] = new_counts.get(key, 0) + 1
                name = f"{name} (new)"
            elif item.license_expression != old_map[item.component_name][0]:
                updated_counts[key] = updated_counts.get(key, 0) + 1
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
                new_counts.get(category, 0) if (old_issues is not None) else None,
                updated_counts.get(category, 0) if (old_issues is not None) else None,
                components.get(category, []),
            )
            for category, count in counts.items()
        ],
        key=lambda kv: kv[0].value,
    )


def _licenses_cell(choices: list[sbom.models.licenses.Choice]) -> htm.Element | str:
    if not choices:
        return "-"
    badges = [badge for choice in choices for badge in _choice_badges(choice)]
    return htm.div(".d-flex.flex-column.gap-1")[*badges]


def _load_license_issues(issues: list[str]) -> list[sbom.models.licenses.Issue]:
    return [sbom.models.licenses.Issue.model_validate(json.loads(i)) for i in issues]


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


def _outdated_tool_section(block: htm.Block, task_result: results.SBOMToolScore):
    block.h3["Outdated tools"]
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
                if "Apache Trusted Releases" in result.key:
                    block.p[
                        f"""The last version of ATR used on this SBOM was
                            {result.used_version} but ATR is currently version
                            {result.available_version}."""
                    ]
                else:
                    block.p[
                        f"""The {result.key} is outdated. The used version is
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
                            determine if the {result.key} is outdated:
                            {result.kind.upper()}."""
                    ]
    else:
        block.p["No outdated tools found."]


def _phase_nav(block: htm.Block, release: sql.Release) -> None:
    back_url = ""
    back_anchor = ""
    phase: Literal["COMPOSE", "VOTE"] = "COMPOSE"
    match release.phase:
        case sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            back_url = util.as_url(compose.selected, project_key=release.project.key, version_key=release.version)
            back_anchor = f"Compose {release.project.short_display_name} {release.version}"
            phase = "COMPOSE"
        case sql.ReleasePhase.RELEASE_CANDIDATE:
            back_url = util.as_url(vote.selected, project_key=release.project.key, version_key=release.version)
            back_anchor = f"Vote on {release.project.short_display_name} {release.version}"
            phase = "VOTE"

    render.html_nav(
        block,
        back_url=back_url,
        back_anchor=back_anchor,
        phase=phase,
    )


def _score_result(task: sql.Task | None) -> results.SBOMToolScore | None:
    if task is None:
        return None
    return task.result if isinstance(task.result, results.SBOMToolScore) else None


def _score_unavailable(task: sql.Task | None) -> str:
    # Why there is no score to read. Says something only where _score_result found nothing
    if task is None:
        return "No SBOM score found."
    if (task.status == sql.TaskStatus.QUEUED) or (task.status == sql.TaskStatus.ACTIVE):
        return "SBOM score is being computed."
    if task.status == sql.TaskStatus.FAILED:
        return f"SBOM score task failed: {task.error}"
    return "No SBOM score found."


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


def _update_worst_severity(severities: list[str], vuln_severity: str, worst: int) -> int:
    try:
        sev_index = severities.index(vuln_severity)
    except ValueError:
        sev_index = 99
    worst = min(worst, sev_index)
    return worst


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
        vuln_id = vuln.id or "Unknown"
        vuln_summary = vuln.summary
        vuln_refs = []
        if vuln.references is not None:
            vuln_refs = [r for r in vuln.references if r.get("type", "") == "WEB"]
        vuln_primary_ref = vuln_refs[0] if (len(vuln_refs) > 0) else {}
        vuln_modified = vuln.modified or "Unknown"

        vuln_severity = _extract_vulnerability_severity(vuln)
        worst = _update_worst_severity(severities, vuln_severity, worst)

        is_new = _vulnerability_is_new(vuln_id, vuln_severity, component.purl, previous_vulns)
        if is_new:
            new = new + 1
        display_id, vulnerability_url = _vulnerability_display(vuln_id, vuln_primary_ref, vuln.references)
        # We only show the link if it's a valid web link
        if vulnerability_url.startswith("http"):
            vuln_header = [htm.a(href=vulnerability_url, target="_blank")[htm.strong(".me-2")[display_id]]]
        else:
            vuln_header = [htm.strong(".me-2")[display_id]]
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

        # cmarkgfm will refuse to write unsafe strings into the html
        # audit_guidance CMARK_OPT_SAFE is the default option in cmarkgfm and it can't be set
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


def _vulnerability_display(
    vuln_id: str, vuln_primary_ref: dict[str, Any], references: list[dict[str, Any]] | None
) -> tuple[str, str]:
    # A CVE alias, where present, is both the friendlier label and a link that matches it
    cve = _cve_reference(references or [])
    if cve is not None:
        return cve
    return vuln_id, vuln_primary_ref.get("url", "")


def _vulnerability_is_new(
    vuln_id: str,
    vuln_severity: str,
    purl: str,
    previous_vulns: dict[str, tuple[str, list[str]]] | None,
) -> bool:
    if previous_vulns is None:
        return False
    return (
        (vuln_id not in previous_vulns)
        or (previous_vulns[vuln_id][0] != vuln_severity)
        or (purl not in previous_vulns[vuln_id][1])
    )


def _vulnerability_results_from_bom(
    vulns: list[results.CdxVulnerabilityDetail],
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
