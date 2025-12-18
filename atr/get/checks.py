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

import pathlib
from collections.abc import Callable
from typing import NamedTuple

import asfquart.base as base
import htpy

import atr.blueprints.get as get
import atr.db as db
import atr.get.download as download
import atr.get.ignores as ignores
import atr.get.report as report
import atr.get.sbom as sbom
import atr.get.vote as vote
import atr.htm as htm
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.template as template
import atr.util as util
import atr.web as web


class FileStats(NamedTuple):
    file_pass_before: int
    file_warn_before: int
    file_err_before: int
    file_pass_after: int
    file_warn_after: int
    file_err_after: int
    member_pass_before: int
    member_warn_before: int
    member_err_before: int
    member_pass_after: int
    member_warn_after: int
    member_err_after: int

    @property
    def total_pass_before(self) -> int:
        return self.file_pass_before + self.member_pass_before

    @property
    def total_warn_before(self) -> int:
        return self.file_warn_before + self.member_warn_before

    @property
    def total_err_before(self) -> int:
        return self.file_err_before + self.member_err_before

    @property
    def total_pass_after(self) -> int:
        return self.file_pass_after + self.member_pass_after

    @property
    def total_warn_after(self) -> int:
        return self.file_warn_after + self.member_warn_after

    @property
    def total_err_after(self) -> int:
        return self.file_err_after + self.member_err_after


async def get_file_totals(release: sql.Release, session: web.Committer | None) -> FileStats:
    """Get file level check totals after ignores are applied."""
    if release.committee is None:
        raise ValueError("Release has no committee")

    base_path = util.release_directory(release)
    paths = [path async for path in util.paths_recursive(base_path)]

    async with storage.read(session) as read:
        ragp = read.as_general_public()
        match_ignore = await ragp.checks.ignores_matcher(release.committee.name)

    _, totals = await _compute_stats(release, paths, match_ignore)
    return totals


@get.public("/checks/<project_name>/<version_name>")
async def selected(session: web.Committer | None, project_name: str, version_name: str) -> str:
    """Show the file checks for a release candidate."""
    async with db.session() as data:
        release = await data.release(
            project_name=project_name,
            version=version_name,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            _committee=True,
        ).demand(base.ASFQuartException("Release does not exist", errorcode=404))

    if release.committee is None:
        raise ValueError("Release has no committee")

    base_path = util.release_directory(release)
    paths = [path async for path in util.paths_recursive(base_path)]
    paths.sort()

    async with storage.read(session) as read:
        ragp = read.as_general_public()
        match_ignore = await ragp.checks.ignores_matcher(release.committee.name)

    per_file_stats, totals = await _compute_stats(release, paths, match_ignore)

    page = htm.Block()
    _render_header(page, release)
    _render_summary(page, totals, paths, per_file_stats)
    _render_checks_table(page, release, paths, per_file_stats)
    _render_ignores_section(page, release)
    _render_debug_table(page, paths, per_file_stats)

    return await template.blank(
        f"File checks for {release.project.short_display_name} {release.version}",
        content=page.collect(),
    )


async def _compute_stats(  # noqa: C901
    release: sql.Release,
    paths: list[pathlib.Path],
    match_ignore: Callable[[sql.CheckResult], bool],
) -> tuple[dict[pathlib.Path, FileStats], FileStats]:
    per_file: dict[pathlib.Path, dict[str, int]] = {
        p: {
            "file_pass_before": 0,
            "file_warn_before": 0,
            "file_err_before": 0,
            "file_pass_after": 0,
            "file_warn_after": 0,
            "file_err_after": 0,
            "member_pass_before": 0,
            "member_warn_before": 0,
            "member_err_before": 0,
            "member_pass_after": 0,
            "member_warn_after": 0,
            "member_err_after": 0,
        }
        for p in paths
    }

    if release.latest_revision_number is None:
        # TODO: Or raise an exception?
        empty_stats = FileStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        return {p: empty_stats for p in paths}, empty_stats

    async with db.session() as data:
        check_results = await data.check_result(
            release_name=release.name,
            revision_number=release.latest_revision_number,
        ).all()

    for cr in check_results:
        if not cr.primary_rel_path:
            continue

        file_path = pathlib.Path(cr.primary_rel_path)
        if file_path not in per_file:
            continue

        is_member = cr.member_rel_path is not None
        is_ignored = match_ignore(cr)
        prefix = "member" if is_member else "file"

        if cr.status == sql.CheckResultStatus.SUCCESS:
            per_file[file_path][f"{prefix}_pass_before"] += 1
            per_file[file_path][f"{prefix}_pass_after"] += 1
        elif cr.status == sql.CheckResultStatus.WARNING:
            per_file[file_path][f"{prefix}_warn_before"] += 1
            if not is_ignored:
                per_file[file_path][f"{prefix}_warn_after"] += 1
        else:
            per_file[file_path][f"{prefix}_err_before"] += 1
            if not is_ignored:
                per_file[file_path][f"{prefix}_err_after"] += 1

    per_file_stats = {p: FileStats(**c) for p, c in per_file.items()}

    total_counts = {
        "file_pass_before": 0,
        "file_warn_before": 0,
        "file_err_before": 0,
        "file_pass_after": 0,
        "file_warn_after": 0,
        "file_err_after": 0,
        "member_pass_before": 0,
        "member_warn_before": 0,
        "member_err_before": 0,
        "member_pass_after": 0,
        "member_warn_after": 0,
        "member_err_after": 0,
    }
    for stats in per_file_stats.values():
        for field in total_counts:
            total_counts[field] += getattr(stats, field)

    return per_file_stats, FileStats(**total_counts)


def _render_checks_table(
    page: htm.Block,
    release: sql.Release,
    paths: list[pathlib.Path],
    per_file_stats: dict[pathlib.Path, FileStats],
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
        htpy.th(".py-2.text-center", style="width: 5em")["Pass"],
        htpy.th(".py-2.text-center", style="width: 5em")["Warning"],
        htpy.th(".py-2.text-center", style="width: 5em")["Error"],
        htpy.th(".py-2.text-end.pe-3")[""],
    ]
    table.append(thead.collect())

    tbody = htm.Block(htpy.tbody)
    for path in paths:
        stats = per_file_stats.get(path, FileStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        _render_file_row(tbody, release, path, stats)
    table.append(tbody.collect())

    page.div(".table-responsive.card.mb-4")[table.collect()]


def _render_debug_table(
    page: htm.Block,
    paths: list[pathlib.Path],
    per_file_stats: dict[pathlib.Path, FileStats],
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

    tbody = htm.Block(htpy.tbody)
    for path in paths:
        stats = per_file_stats.get(path, FileStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        tbody.tr[
            htpy.td(class_="text-start")[htpy.code[str(path)]],
            htpy.td(style=stripe_a)[str(stats.file_pass_before)],
            htpy.td(style=stripe_a)[str(stats.file_warn_before)],
            htpy.td(style=stripe_a)[str(stats.file_err_before)],
            htpy.td(style=stripe_b)[str(stats.file_pass_after)],
            htpy.td(style=stripe_b)[str(stats.file_warn_after)],
            htpy.td(style=stripe_b)[str(stats.file_err_after)],
            htpy.td(style=stripe_a)[str(stats.member_pass_before)],
            htpy.td(style=stripe_a)[str(stats.member_warn_before)],
            htpy.td(style=stripe_a)[str(stats.member_err_before)],
            htpy.td(style=stripe_b)[str(stats.member_pass_after)],
            htpy.td(style=stripe_b)[str(stats.member_warn_after)],
            htpy.td(style=stripe_b)[str(stats.member_err_after)],
            htpy.td(style=stripe_a)[str(stats.total_pass_before)],
            htpy.td(style=stripe_a)[str(stats.total_warn_before)],
            htpy.td(style=stripe_a)[str(stats.total_err_before)],
            htpy.td(style=stripe_b)[str(stats.total_pass_after)],
            htpy.td(style=stripe_b)[str(stats.total_warn_after)],
            htpy.td(style=stripe_b)[str(stats.total_err_after)],
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
    path: pathlib.Path,
    stats: FileStats,
) -> None:
    path_str = str(path)
    num_style = "font-size: 1.1rem;"

    pass_count = stats.file_pass_after
    warn_count = stats.file_warn_after
    err_count = stats.file_err_after
    has_checks_before = (stats.file_pass_before + stats.file_warn_before + stats.file_err_before) > 0
    has_checks_after = (pass_count + warn_count + err_count) > 0

    report_url = util.as_url(
        report.selected_path,
        project_name=release.project.name,
        version_name=release.version,
        rel_path=path_str,
    )
    download_url = util.as_url(
        download.path,
        project_name=release.project.name,
        version_name=release.version,
        file_path=path_str,
    )
    sbom_url = util.as_url(sbom.report, project=release.project.name, version=release.version, file_path=path_str)

    if not has_checks_before:
        path_display = htpy.code(".text-muted")[path_str]
        pass_cell = htpy.span(".text-muted", style=num_style)["-"]
        warn_cell = htpy.span(".text-muted", style=num_style)["-"]
        err_cell = htpy.span(".text-muted", style=num_style)["-"]
        report_btn = htpy.span(".btn.btn-sm.btn-outline-secondary.disabled")["No checks"]
    elif not has_checks_after:
        path_display = htpy.code[path_str]
        pass_cell = htpy.span(".text-muted", style=num_style)["0"]
        warn_cell = htpy.span(".text-muted", style=num_style)["0"]
        err_cell = htpy.span(".text-muted", style=num_style)["0"]
        report_btn = htpy.a(".btn.btn-sm.btn-outline-secondary", href=report_url)["Show details"]
    elif err_count > 0:
        path_display = htpy.strong[htpy.code(".text-danger")[path_str]]
        pass_cell = (
            htpy.span(".text-success", style=num_style)[str(pass_count)]
            if pass_count > 0
            else htpy.span(".text-muted", style=num_style)["0"]
        )
        warn_cell = (
            htpy.span(".text-warning", style=num_style)[str(warn_count)]
            if warn_count > 0
            else htpy.span(".text-muted", style=num_style)["0"]
        )
        err_cell = htpy.span(".text-danger.fw-bold", style=num_style)[str(err_count)]
        report_btn = htpy.a(".btn.btn-sm.btn-outline-danger", href=report_url)["Show details"]
    elif warn_count > 0:
        path_display = htpy.strong[htpy.code(".text-warning")[path_str]]
        pass_cell = (
            htpy.span(".text-success", style=num_style)[str(pass_count)]
            if pass_count > 0
            else htpy.span(".text-muted", style=num_style)["0"]
        )
        warn_cell = htpy.span(".text-warning.fw-bold", style=num_style)[str(warn_count)]
        err_cell = htpy.span(".text-muted", style=num_style)["0"]
        report_btn = htpy.a(".btn.btn-sm.btn-outline-warning", href=report_url)["Show details"]
    else:
        path_display = htpy.code[path_str]
        pass_cell = htpy.span(".text-success", style=num_style)[str(pass_count)]
        warn_cell = htpy.span(".text-muted", style=num_style)["0"]
        err_cell = htpy.span(".text-muted", style=num_style)["0"]
        report_btn = htpy.a(".btn.btn-sm.btn-outline-success", href=report_url)["Show details"]

    # <a href="{{ as_url(get.sbom.report, project=project_name, version=version_name, file_path=path) }}"
    # class="btn btn-sm btn-outline-secondary">Show SBOM</a>
    sbom_btn = None
    if path.suffixes[-2:] == [".cdx", ".json"]:
        sbom_btn = htpy.a(".btn.btn-sm.btn-outline-secondary", href=sbom_url)["SBOM report"]
    download_btn = htpy.a(".btn.btn-sm.btn-outline-secondary", href=download_url)["Download"]

    tbody.tr[
        htpy.td(".py-2.ps-3")[path_display],
        htpy.td(".py-2.text-center")[pass_cell],
        htpy.td(".py-2.text-center")[warn_cell],
        htpy.td(".py-2.text-center")[err_cell],
        htpy.td(".text-end.text-nowrap.py-2.pe-3")[
            htpy.div(".d-flex.justify-content-end.align-items-center.gap-2")[
                report_btn,
                sbom_btn,
                download_btn,
            ],
        ],
    ]


def _render_header(page: htm.Block, release: sql.Release) -> None:
    shared.distribution.html_nav(
        page,
        back_url=util.as_url(vote.selected, project_name=release.project.name, version_name=release.version),
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
    if release.committee is None:
        return

    # TODO: We should choose a consistent " ..." or "... " style
    page.h2["Check ignores"]
    page.p[
        "Committee members can configure rules to ignore specific check results. "
        "Ignored checks are excluded from the counts shown above.",
    ]
    ignores_url = util.as_url(ignores.ignores, committee_name=release.committee.name)
    page.div[htpy.a(".btn.btn-outline-primary", href=ignores_url)["Manage check ignores"],]


def _render_summary(
    page: htm.Block,
    totals: FileStats,
    paths: list[pathlib.Path],
    per_file_stats: dict[pathlib.Path, FileStats],
) -> None:
    files_with_errors = sum(1 for s in per_file_stats.values() if s.file_err_after > 0)
    files_with_warnings = sum(1 for s in per_file_stats.values() if (s.file_warn_after > 0) and (s.file_err_after == 0))
    files_passed = sum(
        1
        for s in per_file_stats.values()
        if (s.file_pass_after > 0) and (s.file_warn_after == 0) and (s.file_err_after == 0)
    )
    files_skipped = len(paths) - files_passed - files_with_warnings - files_with_errors

    file_word = "file" if (len(paths) == 1) else "files"
    passed_word = "file passed" if (files_passed == 1) else "files passed"
    warn_file_word = "file has" if (files_with_warnings == 1) else "files have"
    err_file_word = "file has" if (files_with_errors == 1) else "files have"
    skipped_word = "file did not require checking" if (files_skipped == 1) else "files did not require checking"
    no_errors_word = "no" if ((files_passed > 0) or (files_with_warnings > 0)) else "No"

    page.p[
        f"Showing check results for {len(paths)} {file_word}. ",
        f"{files_passed} {passed_word} all checks, " if (files_passed > 0) else "",
        f"{files_with_warnings} {warn_file_word} warnings, " if (files_with_warnings > 0) else "",
        f"{files_with_errors} {err_file_word} errors."
        if (files_with_errors > 0)
        else f"{no_errors_word} files have errors.",
        f" {files_skipped} {skipped_word}." if (files_skipped > 0) else "",
    ]

    check_word = "check" if (totals.file_pass_after == 1) else "checks"
    warn_word = "warning" if (totals.file_warn_after == 1) else "warnings"
    err_word = "error" if (totals.file_err_after == 1) else "errors"

    summary_div = htm.Block(htm.div, classes=".d-flex.flex-wrap.gap-4.mb-3")
    summary_div.span(".text-success")[
        htpy.i(".bi.bi-check-circle-fill.me-2"),
        f"{totals.file_pass_after} {check_word} passed",
    ]
    if totals.file_warn_after > 0:
        summary_div.span(".text-warning")[
            htpy.i(".bi.bi-exclamation-triangle-fill.me-2"),
            f"{totals.file_warn_after} {warn_word}",
        ]
    else:
        summary_div.span(".text-muted")[
            htpy.i(".bi.bi-exclamation-triangle.me-2"),
            "0 warnings",
        ]
    if totals.file_err_after > 0:
        summary_div.span(".text-danger")[
            htpy.i(".bi.bi-x-circle-fill.me-2"),
            f"{totals.file_err_after} {err_word}",
        ]
    else:
        summary_div.span(".text-muted")[
            htpy.i(".bi.bi-x-circle.me-2"),
            "0 errors",
        ]
    page.append(summary_div.collect())
