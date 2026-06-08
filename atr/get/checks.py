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

import collections
from collections.abc import Callable
from typing import Literal, NamedTuple

import asfquart.base as base
import htpy
import quart

import atr.blueprints.get as get
import atr.classify as classify
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get.download as download
import atr.get.report as report
import atr.get.sbom as sbom
import atr.get.vote as vote
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.post as post
import atr.render as render
import atr.shared.draft as draft
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.template as template
import atr.util as util
import atr.web as web


class FileStats(NamedTuple):
    file_before: collections.Counter[sql.CheckResultStatus]
    file_after: collections.Counter[sql.CheckResultStatus]
    member_before: collections.Counter[sql.CheckResultStatus]
    member_after: collections.Counter[sql.CheckResultStatus]

    def total_before(self, status: sql.CheckResultStatus) -> int:
        return self.file_before[status] + self.member_before[status]

    def total_after(self, status: sql.CheckResultStatus) -> int:
        return self.file_after[status] + self.member_after[status]


async def get_file_totals(release: sql.Release, session: web.Committer | None) -> FileStats:
    """Get file level check totals after ignores are applied."""
    base_path = paths.release_directory(release)
    all_paths = [path async for path in util.paths_recursive(base_path)]

    async with storage.read(session) as read:
        ragp = read.as_general_public()
        match_ignore = await ragp.checks.ignores_matcher(release.safe_project_key)

    _, totals = await _compute_stats(release, all_paths, match_ignore)
    return totals


@get.typed
async def selected(
    session: web.Public, _checks: Literal["checks"], project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> str:
    """
    URL: /checks/<project_key>/<version_key>
    Show the file checks for a release candidate.
    """
    async with db.session() as data:
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            _committee=True,
            _project_release_policy=True,
        ).demand(base.ASFQuartException("Release does not exist", errorcode=404))

    if release.committee is None:
        raise ValueError("Release has no committee")

    base_path = paths.release_directory(release)
    all_paths = [path async for path in util.paths_recursive(base_path)]
    all_paths.sort()

    async with storage.read(session) as read:
        ragp = read.as_general_public()
        match_ignore = await ragp.checks.ignores_matcher(release.safe_project_key)
        info = await ragp.releases.path_info(release, all_paths)

    per_file_stats, totals = await _compute_stats(release, all_paths, match_ignore)

    page = htm.Block()
    _render_header(page, release)
    _render_summary(page, totals, all_paths, per_file_stats)
    if info is not None:
        if banner := render.render_exception_banner(info):
            page.append(banner)
    _render_checks_table(page, release, all_paths, per_file_stats, info)

    return await template.blank(
        f"File checks for {release.project.short_display_name} {release.version}",
        content=page.collect(),
    )


@get.typed
async def selected_revision(
    session: web.Committer,
    _checks: Literal["checks"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
) -> web.QuartResponse:
    """
    URL: /checks/<project_key>/<version_key>/<revision_number>
    Return JSON with ongoing count and HTML fragments for dynamic updates.
    """
    async with db.session() as data:
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
            # _project=True is included in _project_release_policy=True
            _project_release_policy=True,
        ).demand(base.ASFQuartException("Release does not exist", errorcode=404))

    base_path = paths.release_directory(release)
    all_paths = [path async for path in util.paths_recursive(base_path)]
    all_paths.sort()

    async with storage.read(session) as read:
        ragp = read.as_general_public()
        info = await ragp.releases.path_info(release, all_paths)

    ongoing_count = await interaction.tasks_ongoing(project_key, version_key, revision_number)

    checks_summary_elem = render.render_checks_summary(info, project_key, version_key)
    checks_summary_html = str(checks_summary_elem) if checks_summary_elem else ""

    exception_banner_html = ""
    if info is not None:
        banner_elem = render.render_exception_banner(info)
        if banner_elem is not None:
            exception_banner_html = str(banner_elem)

    delete_file_forms: dict[str, str] = {}
    if release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
        for path in all_paths:
            delete_file_forms[str(path)] = str(
                await form.render(
                    model_cls=draft.DeleteFileForm,
                    action=util.as_url(
                        post.draft.delete_file, project_key=str(project_key), version_key=str(version_key)
                    ),
                    form_classes=".d-inline-block.m-0",
                    submit_classes="btn-sm btn-outline-danger",
                    submit_label="Delete",
                    empty=True,
                    defaults={"file_path": str(path)},
                    confirm=(
                        "Are you sure you want to delete this file? "
                        "This will also delete any associated metadata files. "
                        "This cannot be undone."
                    ),
                )
            )

    files_table_html = await template.render(
        "check-selected-path-table.html",
        paths=all_paths,
        info=info,
        project_key=str(project_key),
        version_key=str(version_key),
        release=release,
        phase=release.phase.value,
        delete_file_forms=delete_file_forms,
        csrf_input=str(form.csrf_input()),
        exception_banner_html=exception_banner_html,
    )

    return quart.jsonify(
        {
            "ongoing": ongoing_count,
            "checks_summary_html": checks_summary_html,
            "files_table_html": files_table_html,
        }
    )


def _classification_badge_cell(
    info: datatypes.PathInfo | None, path: safe.RelPath, severity: sql.CheckResultStatus | None
) -> htm.Element:
    file_type = info.file_types.get(path) if (info is not None) else None
    match file_type:
        case classify.FileType.DISALLOWED:
            label, title = "bad", "Disallowed file"
        case classify.FileType.SOURCE:
            label, title = "src", "Source artifact"
        case classify.FileType.METADATA:
            label, title = "meta", "Metadata file"
        case classify.FileType.DOCS | classify.FileType.BINARY | None:
            label, title = "bin", "Binary artifact"
    if severity is not None:
        icon_class = render.PATH_STYLE_CLASS.get(severity, "text-success")
    else:
        icon_class = "text-success"
    return htpy.td(f".text-center.px-0.py-0.atr-sans.{icon_class}")[
        htpy.span(".atr-classification-badge", title=title)[label]
    ]


async def _compute_stats(
    release: sql.Release,
    paths: list[safe.RelPath],
    match_ignore: Callable[[sql.CheckResult], bool],
) -> tuple[dict[safe.RelPath, FileStats], FileStats]:
    per_file = {path: _file_stats_empty() for path in paths}

    if release.latest_revision_number is None:
        return per_file, _file_stats_empty()

    async with db.session() as data:
        check_results = await interaction.checks_for(release, caller_data=data)

    for check_result in check_results:
        if not check_result.primary_rel_path:
            continue

        file_path = safe.RelPath(check_result.primary_rel_path)
        stats = per_file.get(file_path)
        if stats is None:
            continue

        _file_stats_result_add(stats, check_result, match_ignore(check_result))

    totals = _file_stats_empty()
    for stats in per_file.values():
        totals.file_before.update(stats.file_before)
        totals.file_after.update(stats.file_after)
        totals.member_before.update(stats.member_before)
        totals.member_after.update(stats.member_after)

    return per_file, totals


def _count_cell(count: int, status: sql.CheckResultStatus, has_checks_before: bool, num_style: str) -> htm.Element:
    if count > 0:
        cell_class = render.CELL_TEXT_CLASS[status]
        return htpy.td(".py-2.text-center")[htpy.span(f".{cell_class}.fw-bold", style=num_style)[str(count)]]
    if not has_checks_before:
        return htpy.td(".py-2.text-center")[htpy.span(".text-muted", style=num_style)["-"]]
    return htpy.td(".py-2.text-center")[htpy.span(".text-muted", style=num_style)["0"]]


def _error_count(counts: collections.Counter[sql.CheckResultStatus]) -> int:
    return (
        counts[sql.CheckResultStatus.CONCERN]
        + counts[sql.CheckResultStatus.BLOCKER]
        + counts[sql.CheckResultStatus.EXCEPTION]
    )


def _file_stats_empty() -> FileStats:
    return FileStats(
        file_before=collections.Counter[sql.CheckResultStatus](),
        file_after=collections.Counter[sql.CheckResultStatus](),
        member_before=collections.Counter[sql.CheckResultStatus](),
        member_after=collections.Counter[sql.CheckResultStatus](),
    )


def _file_stats_result_add(stats: FileStats, check_result: sql.CheckResult, is_ignored: bool) -> None:
    if check_result.member_rel_path is None:
        before = stats.file_before
        after = stats.file_after
    else:
        before = stats.member_before
        after = stats.member_after

    before[check_result.status] += 1
    if (check_result.status == sql.CheckResultStatus.NOTE) or (not is_ignored):
        after[check_result.status] += 1


def _issue_count_after(stats: FileStats) -> int:
    return stats.total_after(sql.CheckResultStatus.CONCERN) + stats.total_after(sql.CheckResultStatus.BLOCKER)


def _path_display(path_str: str, severity: sql.CheckResultStatus | None, has_checks_before: bool) -> htm.Element:
    if (severity is not None) and (severity_class := render.PATH_STYLE_CLASS.get(severity)):
        return htpy.strong[htpy.code(f".{severity_class}")[path_str]]
    if not has_checks_before:
        return htpy.code(".text-muted")[path_str]
    return htpy.code[path_str]


def _render_checks_table(
    page: htm.Block,
    release: sql.Release,
    paths: list[safe.RelPath],
    per_file_stats: dict[safe.RelPath, FileStats],
    info: datatypes.PathInfo | None,
) -> None:
    if not paths:
        page.div(".alert.alert-info")["This release candidate does not have any files."]
        return

    table = htm.Block(htpy.table, classes=".table.table-striped.align-middle.table-sm.mb-0.border")

    thead = htm.Block(htpy.thead, classes=".table-light")
    header_cells: list[htm.Element] = [
        htpy.th(".py-2.text-center.px-0.atr-w-4em")[""],
        htpy.th(".py-2")["Path"],
    ]
    for status in render.TABLE_STATUSES:
        header_cells.append(htpy.th(".py-2.text-center.atr-w-6em")[render.COLUMN_HEADERS[status]])
    header_cells.append(htpy.th(".py-2.text-end.pe-3")[""])
    thead.tr[*header_cells]
    table.append(thead.collect())

    empty_stats = _file_stats_empty()
    tbody = htm.Block(htpy.tbody)
    for path in paths:
        _render_file_row(tbody, release, path, per_file_stats.get(path, empty_stats), info)
    table.append(tbody.collect())

    page.div(".table-responsive.card.mb-4")[table.collect()]


def _render_file_row(
    tbody: htm.Block,
    release: sql.Release,
    path: safe.RelPath,
    stats: FileStats,
    info: datatypes.PathInfo | None,
) -> None:
    path_str = str(path)
    num_style = "font-size: 1.1rem;"

    counts_after = {status: stats.total_after(status) for status in sql.CheckResultStatus}
    counts_before = {status: stats.total_before(status) for status in sql.CheckResultStatus}
    has_checks_before = sum(counts_before.values()) > 0
    severity = render.highest_severity(counts_after)

    report_url = util.as_url(
        report.selected_path,
        project_key=release.project.key,
        version_key=release.version,
        rel_path=path_str,
    )
    download_url = util.as_url(
        download.path,
        project_key=release.project.key,
        version_key=release.version,
        file_path=path_str,
    )
    sbom_url = util.as_url(
        sbom.report, project_key=release.project.key, version_key=release.version, file_path=path_str
    )

    path_display = _path_display(path_str, severity, has_checks_before)
    report_btn = _report_button(severity, counts_after, has_checks_before, report_url)
    badge_cell = _classification_badge_cell(info, path, severity)

    count_cells: list[htm.Element] = []
    for status in render.TABLE_STATUSES:
        count_cells.append(_count_cell(counts_after[status], status, has_checks_before, num_style))

    sbom_btn = None
    if path.as_path().suffixes[-2:] == [".cdx", ".json"]:
        sbom_btn = htpy.a(".btn.btn-sm.btn-outline-secondary", href=sbom_url)["SBOM report"]
    download_btn = htpy.a(".btn.btn-sm.btn-outline-secondary", href=download_url)["Download"]

    tbody.tr[
        badge_cell,
        htpy.td(".py-2")[path_display],
        *count_cells,
        htpy.td(".text-end.text-nowrap.py-2.pe-3")[
            htpy.div(".d-flex.justify-content-end.align-items-center.gap-2")[
                report_btn,
                sbom_btn,
                download_btn,
            ],
        ],
    ]


def _render_header(page: htm.Block, release: sql.Release) -> None:
    render.html_nav(
        page,
        back_url=util.as_url(vote.selected, project_key=release.project.key, version_key=release.version),
        back_anchor=f"Vote on {release.project.short_display_name} {release.version}",
        phase="VOTE",
    )

    page.h1[
        "File checks for ",
        htm.strong[release.project.short_display_name],
        " ",
        htm.em[release.version],
    ]


def _render_summary(
    page: htm.Block,
    totals: FileStats,
    paths: list[safe.RelPath],
    per_file_stats: dict[safe.RelPath, FileStats],
) -> None:
    files_with_issues = sum(1 for s in per_file_stats.values() if _issue_count_after(s) > 0)
    files_with_suggestions = sum(
        1
        for s in per_file_stats.values()
        if (s.total_after(sql.CheckResultStatus.SUGGESTION) > 0)
        and (s.total_after(sql.CheckResultStatus.EXCEPTION) == 0)
        and (_issue_count_after(s) == 0)
    )
    files_with_notes = sum(
        1
        for s in per_file_stats.values()
        if (s.total_after(sql.CheckResultStatus.NOTE) > 0)
        and (s.total_after(sql.CheckResultStatus.SUGGESTION) == 0)
        and (s.total_after(sql.CheckResultStatus.EXCEPTION) == 0)
        and (_issue_count_after(s) == 0)
    )
    files_with_exceptions = sum(
        1 for s in per_file_stats.values() if s.total_after(sql.CheckResultStatus.EXCEPTION) > 0
    )
    files_with_any_status = sum(
        1 for s in per_file_stats.values() if sum(s.total_after(status) for status in sql.CheckResultStatus) > 0
    )
    files_skipped = len(paths) - files_with_any_status

    file_word = "file" if (len(paths) == 1) else "files"
    note_file_word = "file has" if (files_with_notes == 1) else "files have"
    suggestion_file_word = "file has" if (files_with_suggestions == 1) else "files have"
    issue_file_word = "file has" if (files_with_issues == 1) else "files have"
    exception_file_word = "file has" if (files_with_exceptions == 1) else "files have"
    skipped_word = "file did not require checking" if (files_skipped == 1) else "files did not require checking"
    no_issues_word = "no" if ((files_with_notes > 0) or (files_with_suggestions > 0)) else "No"

    page.p[
        f"Showing check results for {len(paths)} {file_word}. ",
        f"{files_with_notes} {note_file_word} only notes, " if (files_with_notes > 0) else "",
        f"{files_with_suggestions} {suggestion_file_word} suggestions, " if (files_with_suggestions > 0) else "",
        f"{files_with_issues} {issue_file_word} issues."
        if (files_with_issues > 0)
        else f"{no_issues_word} files have issues.",
        f" {files_with_exceptions} {exception_file_word} tooling exceptions." if (files_with_exceptions > 0) else "",
        f" {files_skipped} {skipped_word}." if (files_skipped > 0) else "",
    ]

    note_count = totals.total_after(sql.CheckResultStatus.NOTE)
    suggestion_count = totals.total_after(sql.CheckResultStatus.SUGGESTION)
    issue_count = _issue_count_after(totals)
    exception_count = totals.total_after(sql.CheckResultStatus.EXCEPTION)
    note_word = util.plural(note_count, "note", include_count=False)
    suggestion_word = util.plural(suggestion_count, "suggestion", include_count=False)
    issue_word = util.plural(issue_count, "issue", include_count=False)
    exception_word = util.plural(exception_count, "exception", include_count=False)

    summary_div = htm.Block(htm.div, classes=".d-flex.flex-wrap.gap-4.mb-3")
    summary_div.span(".text-success")[
        htpy.i(".bi.bi-check-circle-fill.me-2"),
        f"{note_count} {note_word}",
    ]
    if suggestion_count > 0:
        summary_div.span(".text-warning")[
            htpy.i(".bi.bi-exclamation-triangle-fill.me-2"),
            f"{suggestion_count} {suggestion_word}",
        ]
    else:
        summary_div.span(".text-muted")[
            htpy.i(".bi.bi-exclamation-triangle.me-2"),
            "0 suggestions",
        ]
    if issue_count > 0:
        summary_div.span(".text-danger")[
            htpy.i(".bi.bi-x-circle-fill.me-2"),
            f"{issue_count} {issue_word}",
        ]
    else:
        summary_div.span(".text-muted")[
            htpy.i(".bi.bi-x-circle.me-2"),
            "0 issues",
        ]
    if exception_count > 0:
        summary_div.span(".atr-text-exception")[
            htpy.i(".bi.bi-cone-striped.me-2"),
            f"{exception_count} {exception_word}",
        ]
    page.append(summary_div.collect())


def _report_button(
    severity: sql.CheckResultStatus | None,
    counts_after: dict[sql.CheckResultStatus, int],
    has_checks_before: bool,
    report_url: str,
) -> htm.Element:
    match severity:
        case sql.CheckResultStatus.BLOCKER:
            return htpy.a(".btn.btn-sm.atr-btn-outline-blocker", href=report_url)["Show details"]
        case sql.CheckResultStatus.EXCEPTION:
            return htpy.a(".btn.btn-sm.btn-outline-danger", href=report_url)["Show details"]
        case sql.CheckResultStatus.CONCERN:
            return htpy.a(".btn.btn-sm.atr-btn-outline-concern", href=report_url)["Show details"]
        case sql.CheckResultStatus.SUGGESTION:
            return htpy.a(".btn.btn-sm.atr-btn-outline-suggestion", href=report_url)["Show details"]
        case sql.CheckResultStatus.NOTE | None:
            if counts_after[sql.CheckResultStatus.NOTE] > 0:
                return htpy.a(".btn.btn-sm.btn-outline-secondary", href=report_url)["Notes"]
            if has_checks_before:
                return htpy.a(".btn.btn-sm.btn-outline-secondary", href=report_url)["Show details"]
            return htpy.span(".btn.btn-sm.btn-outline-secondary.disabled")["No checks"]
