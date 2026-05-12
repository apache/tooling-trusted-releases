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
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get.download as download
import atr.get.ignores as ignores
import atr.get.report as report
import atr.get.sbom as sbom
import atr.get.vote as vote
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.shared.draft as draft
import atr.storage as storage
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
        ).demand(base.ASFQuartException("Release does not exist", errorcode=404))

    if release.committee is None:
        raise ValueError("Release has no committee")

    base_path = paths.release_directory(release)
    all_paths = [path async for path in util.paths_recursive(base_path)]
    all_paths.sort()

    async with storage.read(session) as read:
        ragp = read.as_general_public()
        match_ignore = await ragp.checks.ignores_matcher(release.safe_project_key)

    per_file_stats, totals = await _compute_stats(release, all_paths, match_ignore)

    page = htm.Block()
    _render_header(page, release)
    _render_summary(page, totals, all_paths, per_file_stats)
    _render_checks_table(page, release, all_paths, per_file_stats)
    _render_ignores_section(page, release)
    _render_debug_table(page, all_paths, per_file_stats)

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

    checks_summary_elem = shared.web.render_checks_summary(info, project_key, version_key)
    checks_summary_html = str(checks_summary_elem) if checks_summary_elem else ""

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
    )

    return quart.jsonify(
        {
            "ongoing": ongoing_count,
            "checks_summary_html": checks_summary_html,
            "files_table_html": files_table_html,
        }
    )


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


def _render_checks_table(
    page: htm.Block,
    release: sql.Release,
    paths: list[safe.RelPath],
    per_file_stats: dict[safe.RelPath, FileStats],
) -> None:
    if not paths:
        page.div(".alert.alert-info")["This release candidate does not have any files."]
        return

    table = htm.Block(htpy.table, classes=".table.table-striped.align-middle.table-sm.mb-0.border")

    thead = htm.Block(htpy.thead, classes=".table-light")
    # TODO: We forbid inline styles in Jinja2 through linting
    # But we use it here
    # It is convenient, and we should consider whether or not to allow it
    thead.tr[
        htpy.th(".py-2.ps-3")["Path"],
        htpy.th(".py-2.text-center", style="width: 5em")["Notes"],
        htpy.th(".py-2.text-center", style="width: 5em")["Suggestions"],
        htpy.th(".py-2.text-center", style="width: 5em")["Issues"],
        htpy.th(".py-2.text-end.pe-3")[""],
    ]
    table.append(thead.collect())

    empty_stats = _file_stats_empty()
    tbody = htm.Block(htpy.tbody)
    for path in paths:
        _render_file_row(tbody, release, path, per_file_stats.get(path, empty_stats))
    table.append(tbody.collect())

    page.div(".table-responsive.card.mb-4")[table.collect()]


def _render_debug_table(
    page: htm.Block,
    paths: list[safe.RelPath],
    per_file_stats: dict[safe.RelPath, FileStats],
) -> None:
    # Bootstrap does have striping, but that's for horizontal stripes
    # These are vertical stripes, to make it easier to distinguish collections
    stripe_a = "background-color: #f0f0f0; text-align: center;"
    stripe_b = "background-color: #ffffff; text-align: center;"

    table = htm.Block(htpy.table, classes=".table.table-bordered.table-sm.mb-0.text-center")

    thead = htm.Block(htpy.thead, classes=".table-light")
    thead.tr[
        htpy.th(rowspan="2", style="text-align: center; vertical-align: middle;")["Path"],
        htpy.th(colspan="3", style=stripe_a)["File (before)"],
        htpy.th(colspan="3", style=stripe_b)["File (after)"],
        htpy.th(colspan="3", style=stripe_a)["Member (before)"],
        htpy.th(colspan="3", style=stripe_b)["Member (after)"],
        htpy.th(colspan="3", style=stripe_a)["Total (before)"],
        htpy.th(colspan="3", style=stripe_b)["Total (after)"],
    ]
    thead.tr[
        htpy.th(style=stripe_a)["P"],
        htpy.th(style=stripe_a)["W"],
        htpy.th(style=stripe_a)["E"],
        htpy.th(style=stripe_b)["P"],
        htpy.th(style=stripe_b)["W"],
        htpy.th(style=stripe_b)["E"],
        htpy.th(style=stripe_a)["P"],
        htpy.th(style=stripe_a)["W"],
        htpy.th(style=stripe_a)["E"],
        htpy.th(style=stripe_b)["P"],
        htpy.th(style=stripe_b)["W"],
        htpy.th(style=stripe_b)["E"],
        htpy.th(style=stripe_a)["P"],
        htpy.th(style=stripe_a)["W"],
        htpy.th(style=stripe_a)["E"],
        htpy.th(style=stripe_b)["P"],
        htpy.th(style=stripe_b)["W"],
        htpy.th(style=stripe_b)["E"],
    ]
    table.append(thead.collect())

    empty_stats = _file_stats_empty()
    tbody = htm.Block(htpy.tbody)
    for path in paths:
        stats = per_file_stats.get(path, empty_stats)
        tbody.tr[
            htpy.td(class_="text-start")[htpy.code[str(path)]],
            htpy.td(style=stripe_a)[str(stats.file_before[sql.CheckResultStatus.NOTE])],
            htpy.td(style=stripe_a)[str(stats.file_before[sql.CheckResultStatus.SUGGESTION])],
            htpy.td(style=stripe_a)[str(_error_count(stats.file_before))],
            htpy.td(style=stripe_b)[str(stats.file_after[sql.CheckResultStatus.NOTE])],
            htpy.td(style=stripe_b)[str(stats.file_after[sql.CheckResultStatus.SUGGESTION])],
            htpy.td(style=stripe_b)[str(_error_count(stats.file_after))],
            htpy.td(style=stripe_a)[str(stats.member_before[sql.CheckResultStatus.NOTE])],
            htpy.td(style=stripe_a)[str(stats.member_before[sql.CheckResultStatus.SUGGESTION])],
            htpy.td(style=stripe_a)[str(_error_count(stats.member_before))],
            htpy.td(style=stripe_b)[str(stats.member_after[sql.CheckResultStatus.NOTE])],
            htpy.td(style=stripe_b)[str(stats.member_after[sql.CheckResultStatus.SUGGESTION])],
            htpy.td(style=stripe_b)[str(_error_count(stats.member_after))],
            htpy.td(style=stripe_a)[str(stats.total_before(sql.CheckResultStatus.NOTE))],
            htpy.td(style=stripe_a)[str(stats.total_before(sql.CheckResultStatus.SUGGESTION))],
            htpy.td(style=stripe_a)[str(_total_error_before(stats))],
            htpy.td(style=stripe_b)[str(stats.total_after(sql.CheckResultStatus.NOTE))],
            htpy.td(style=stripe_b)[str(stats.total_after(sql.CheckResultStatus.SUGGESTION))],
            htpy.td(style=stripe_b)[str(_total_error_after(stats))],
        ]
    table.append(tbody.collect())

    page.append(
        htpy.details(".mt-4")[
            htpy.summary["All statistics"],
            htpy.div(".table-responsive.mt-3")[table.collect()],
        ]
    )


def _render_file_row(
    tbody: htm.Block,
    release: sql.Release,
    path: safe.RelPath,
    stats: FileStats,
) -> None:
    path_str = str(path)
    num_style = "font-size: 1.1rem;"

    note_count = stats.file_after[sql.CheckResultStatus.NOTE]
    suggestion_count = stats.file_after[sql.CheckResultStatus.SUGGESTION]
    issue_count = _error_count(stats.file_after)
    before_total = (
        stats.file_before[sql.CheckResultStatus.NOTE]
        + stats.file_before[sql.CheckResultStatus.SUGGESTION]
        + _error_count(stats.file_before)
    )
    has_checks_before = before_total > 0
    has_checks_after = (note_count + suggestion_count + issue_count) > 0

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

    if not has_checks_before:
        path_display = htpy.code(".text-muted")[path_str]
        note_cell = htpy.span(".text-muted", style=num_style)["-"]
        suggestion_cell = htpy.span(".text-muted", style=num_style)["-"]
        issue_cell = htpy.span(".text-muted", style=num_style)["-"]
        report_btn = htpy.span(".btn.btn-sm.btn-outline-secondary.disabled")["No checks"]
    elif not has_checks_after:
        path_display = htpy.code[path_str]
        note_cell = htpy.span(".text-muted", style=num_style)["0"]
        suggestion_cell = htpy.span(".text-muted", style=num_style)["0"]
        issue_cell = htpy.span(".text-muted", style=num_style)["0"]
        report_btn = htpy.a(".btn.btn-sm.btn-outline-secondary", href=report_url)["Show details"]
    elif issue_count > 0:
        path_display = htpy.strong[htpy.code(".text-danger")[path_str]]
        note_cell = (
            htpy.span(".text-success", style=num_style)[str(note_count)]
            if (note_count > 0)
            else htpy.span(".text-muted", style=num_style)["0"]
        )
        suggestion_cell = (
            htpy.span(".text-warning", style=num_style)[str(suggestion_count)]
            if (suggestion_count > 0)
            else htpy.span(".text-muted", style=num_style)["0"]
        )
        issue_cell = htpy.span(".text-danger.fw-bold", style=num_style)[str(issue_count)]
        report_btn = htpy.a(".btn.btn-sm.btn-outline-danger", href=report_url)["Show details"]
    elif suggestion_count > 0:
        path_display = htpy.strong[htpy.code(".text-warning")[path_str]]
        note_cell = (
            htpy.span(".text-success", style=num_style)[str(note_count)]
            if (note_count > 0)
            else htpy.span(".text-muted", style=num_style)["0"]
        )
        suggestion_cell = htpy.span(".text-warning.fw-bold", style=num_style)[str(suggestion_count)]
        issue_cell = htpy.span(".text-muted", style=num_style)["0"]
        report_btn = htpy.a(".btn.btn-sm.btn-outline-warning", href=report_url)["Show details"]
    else:
        path_display = htpy.code[path_str]
        note_cell = htpy.span(".text-success", style=num_style)[str(note_count)]
        suggestion_cell = htpy.span(".text-muted", style=num_style)["0"]
        issue_cell = htpy.span(".text-muted", style=num_style)["0"]
        report_btn = htpy.a(".btn.btn-sm.btn-outline-success", href=report_url)["Show details"]

    # <a href="{{ as_url(get.sbom.report, project=project_key, version=version_key, file_path=path) }}"
    # class="btn btn-sm btn-outline-secondary">Show SBOM</a>
    sbom_btn = None
    if path.as_path().suffixes[-2:] == [".cdx", ".json"]:
        sbom_btn = htpy.a(".btn.btn-sm.btn-outline-secondary", href=sbom_url)["SBOM report"]
    download_btn = htpy.a(".btn.btn-sm.btn-outline-secondary", href=download_url)["Download"]

    tbody.tr[
        htpy.td(".py-2.ps-3")[path_display],
        htpy.td(".py-2.text-center")[note_cell],
        htpy.td(".py-2.text-center")[suggestion_cell],
        htpy.td(".py-2.text-center")[issue_cell],
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


def _render_ignores_section(page: htm.Block, release: sql.Release) -> None:
    # TODO: We should choose a consistent " ..." or "... " style
    page.h2["Check ignores"]
    page.p[
        "Project committee members can configure rules to ignore specific check results. "
        "Ignored checks are excluded from the counts shown above.",
    ]
    ignores_url = util.as_url(ignores.ignores, project_key=release.project.key)
    page.div[htpy.a(".btn.btn-outline-primary", href=ignores_url)["Manage check ignores"],]


def _render_summary(
    page: htm.Block,
    totals: FileStats,
    paths: list[safe.RelPath],
    per_file_stats: dict[safe.RelPath, FileStats],
) -> None:
    files_with_issues = sum(1 for s in per_file_stats.values() if _error_count(s.file_after) > 0)
    files_with_suggestions = sum(
        1
        for s in per_file_stats.values()
        if (s.file_after[sql.CheckResultStatus.SUGGESTION] > 0) and (_error_count(s.file_after) == 0)
    )
    files_with_notes = sum(
        1
        for s in per_file_stats.values()
        if (s.file_after[sql.CheckResultStatus.NOTE] > 0)
        and (s.file_after[sql.CheckResultStatus.SUGGESTION] == 0)
        and (_error_count(s.file_after) == 0)
    )
    files_skipped = len(paths) - files_with_notes - files_with_suggestions - files_with_issues

    file_word = "file" if (len(paths) == 1) else "files"
    note_file_word = "file has" if (files_with_notes == 1) else "files have"
    suggestion_file_word = "file has" if (files_with_suggestions == 1) else "files have"
    issue_file_word = "file has" if (files_with_issues == 1) else "files have"
    skipped_word = "file did not require checking" if (files_skipped == 1) else "files did not require checking"
    no_issues_word = "no" if ((files_with_notes > 0) or (files_with_suggestions > 0)) else "No"

    page.p[
        f"Showing check results for {len(paths)} {file_word}. ",
        f"{files_with_notes} {note_file_word} only notes, " if (files_with_notes > 0) else "",
        f"{files_with_suggestions} {suggestion_file_word} suggestions, " if (files_with_suggestions > 0) else "",
        f"{files_with_issues} {issue_file_word} issues."
        if (files_with_issues > 0)
        else f"{no_issues_word} files have issues.",
        f" {files_skipped} {skipped_word}." if (files_skipped > 0) else "",
    ]

    note_count = totals.file_after[sql.CheckResultStatus.NOTE]
    suggestion_count = totals.file_after[sql.CheckResultStatus.SUGGESTION]
    issue_count = _error_count(totals.file_after)
    note_word = util.plural(note_count, "note", include_count=False)
    suggestion_word = util.plural(suggestion_count, "suggestion", include_count=False)
    issue_word = util.plural(issue_count, "issue", include_count=False)

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
    page.append(summary_div.collect())


def _total_error_after(stats: FileStats) -> int:
    return _error_count(stats.file_after) + _error_count(stats.member_after)


def _total_error_before(stats: FileStats) -> int:
    return _error_count(stats.file_before) + _error_count(stats.member_before)
