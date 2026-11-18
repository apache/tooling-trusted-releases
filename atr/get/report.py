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
import collections
import dataclasses
import datetime
import posixpath
from typing import Final, Literal

import aiofiles.os
import asfquart.base as base

import atr.archives as archives
import atr.attestable as attestable
import atr.blueprints.get as get
import atr.db as db
import atr.errors as errors
import atr.hashes as hashes
import atr.htm as htm
import atr.models.attestable
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.paths as paths
import atr.storage as storage
import atr.template as template
import atr.util as util
import atr.web as web

_MAX_DIFFERING: Final = 100
_MAX_MATCHING: Final = 500


@dataclasses.dataclass
class CompareQuery:
    sibling: str = ""


@dataclasses.dataclass
class ReportQuery(web.PageQuery):
    limit: int = 250


@get.typed
async def compare(
    _session: web.Committer,
    _compare: Literal["compare"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    rel_path: safe.RelPath,
    query_args: CompareQuery,
) -> str:
    async with db.session() as data:
        release = await data.release(project_key=str(project_key), version=str(version_key)).demand(
            base.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404)
        )
    if (release.phase == sql.ReleasePhase.RELEASE) or (release.latest_revision_number is None):
        raise base.ASFQuartException(errors.FILE_NOT_FOUND, errorcode=404)
    recorded = await attestable.load(project_key, version_key, release.safe_latest_revision_number)
    primary = str(rel_path)
    if (recorded is None) or (query_args.sibling not in attestable.cross_format_siblings(recorded, primary)):
        raise base.ASFQuartException(errors.FILE_NOT_FOUND, errorcode=404)
    left = await _compare_paths(release, recorded, primary)
    right = await _compare_paths(release, recorded, query_args.sibling)
    comparison = await asyncio.to_thread(archives.compare_trees, *left, *right)
    if comparison is None:
        raise base.ASFQuartException("Both archives must contain a single root directory", errorcode=404)
    return await template.blank(
        title=f"Compare archives for {release.short_display_name}",
        content=_compare_page(release, primary, query_args.sibling, comparison),
    )


@get.typed
async def selected_path(
    session: web.Public,
    _report: Literal["report"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    rel_path: safe.RelPath,
    query_args: ReportQuery,
) -> str:
    """
    URL: /report/<project_key>/<version_key>/<rel_path>
    Show the report for a specific file.
    """
    try:
        validation.pagination_args_validate(query_args)
    except ValueError as e:
        raise base.ASFQuartException(str(e), errorcode=400)
    validated_path = rel_path.as_path()

    # If the draft is not found, we try to get the release candidate
    async with db.session() as data:
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
            _release_policy=True,
            _project_release_policy=True,
        ).demand(base.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404))
    if release.phase not in (
        sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        sql.ReleasePhase.RELEASE_CANDIDATE,
        sql.ReleasePhase.RELEASE,
    ):
        raise base.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404)

    if release.committee is None:
        raise base.ASFQuartException(errors.RELEASE_NO_COMMITTEE, errorcode=500)

    if release.phase == sql.ReleasePhase.RELEASE:
        revision, attestable_data, file_size = await _published_file(project_key, version_key, str(rel_path))
        uploaded = None
    else:
        # TODO: When we do more than one thing in a dir, we should use the revision directory directly
        abs_path = paths.release_directory(release) / validated_path
        if release.latest_revision_number is None:
            raise base.ASFQuartException("Release has no revision", errorcode=500)

        # Check that the file exists
        if not await aiofiles.os.path.exists(abs_path):
            raise base.ASFQuartException(errors.FILE_NOT_FOUND, errorcode=404)

        modified = int(await aiofiles.os.path.getmtime(abs_path))
        uploaded = datetime.datetime.fromtimestamp(modified, tz=datetime.UTC)
        file_size = await aiofiles.os.path.getsize(abs_path)
        revision = release.safe_latest_revision_number
        attestable_data = await attestable.load(project_key, version_key, revision)

    swhid_dir = None
    if attestable_data is not None:
        swhid_dir = attestable.path_swhid_dir(attestable_data, str(rel_path))

    # Get all check results for this file
    async with storage.read(session) as read:
        ragp = read.as_general_public()
        check_results = await ragp.checks.by_release_path(
            release, validated_path, query_args.offset, query_args.limit, revision
        )
    shown = sum(len(results) for results in check_results.member_results_list.values())
    page = web.page_nav(query_args.offset, query_args.limit, check_results.member_count, shown)

    file_data = {
        "filename": validated_path.name,
        "bytes_size": file_size,
        "uploaded": uploaded,
        "swhid": swhid_dir,
    }

    return await template.render(
        "report-selected-path.html",
        project_key=str(project_key),
        version_key=str(version_key),
        rel_path=str(rel_path),
        package=file_data,
        release=release,
        reconciliation=_reconciliation(check_results.primary_results_list, check_results.member_status_counts),
        primary_results=check_results.primary_results_list,
        member_results=check_results.member_results_list,
        member_count=check_results.member_count,
        member_note_count=check_results.member_note_count,
        limit=query_args.limit,
        page=page,
        format_file_size=util.format_file_size,
    )


def _compare_label(difference: archives.MemberDifference, primary: str, sibling: str) -> str:
    match difference:
        case archives.MemberDifference.CONTENTS:
            return "Contents differ"
        case archives.MemberDifference.EXECUTABLE:
            return "Executable bit differs"
        case archives.MemberDifference.ONLY_LEFT:
            return f"Only in {_compare_suffix(primary)}"
        case archives.MemberDifference.ONLY_RIGHT:
            return f"Only in {_compare_suffix(sibling)}"
        case archives.MemberDifference.TYPE:
            return "File type differs"


def _compare_page(release: sql.Release, primary: str, sibling: str, comparison: archives.TreeComparison) -> htm.Element:
    report_url = util.as_url(
        selected_path, project_key=release.project_key, version_key=release.version, rel_path=primary
    )
    differing = [
        (path, _compare_label(difference, primary, sibling))
        for path, difference in comparison.differing[:_MAX_DIFFERING]
    ]
    matching = [(path,) for path in comparison.matching[:_MAX_MATCHING]]
    page = htm.Block()
    page.p[htm.a(".atr-back-link", href=report_url)[f"← Back to the report for {posixpath.basename(primary)}"]]
    page.h1["Compare archives for ", htm.strong[release.project.short_display_name], " ", htm.em[release.version]]
    page.p[htm.code[primary], " with ", htm.code[sibling]]
    page.h2[f"Files that differ ({len(comparison.differing):,})"]
    _compare_table(page, ("Path", "Difference"), differing, len(comparison.differing))
    with page.block(htm.details) as details:
        details.summary(".btn.btn-outline-success.mb-3")[f"Files that match ({len(comparison.matching):,})"]
        with details.block(htm.div) as matching_block:
            _compare_table(matching_block, ("Path",), matching, len(comparison.matching))
    return page.collect()


async def _compare_paths(
    release: sql.Release, recorded: atr.models.attestable.Attestable, rel_path: str
) -> tuple[safe.StatePath, safe.StatePath]:
    content_hash = attestable.path_hash(recorded, rel_path)
    if content_hash is None:
        raise base.ASFQuartException(errors.FILE_NOT_FOUND, errorcode=404)
    archive_key = hashes.filesystem_archives_key(content_hash)
    extract_dir = paths.get_archives_dir() / release.project_key / release.version / archive_key
    if not await aiofiles.os.path.isdir(extract_dir):
        raise base.ASFQuartException("The extracted archive is no longer available", errorcode=404)
    return paths.release_directory(release) / rel_path, extract_dir


def _compare_suffix(rel_path: str) -> str:
    name = posixpath.basename(rel_path)
    return name.removeprefix(util.archive_format_stem(name) or "")


def _compare_table(block: htm.Block, headers: tuple[str, ...], rows: list[tuple[str, ...]], total: int) -> None:
    if rows:
        with block.block(htm.table, classes=".table.table-sm.table-striped") as table:
            table.thead[htm.tr[[htm.th(scope="col")[header] for header in headers]]]
            with table.block(htm.tbody) as body:
                for path, *cells in rows:
                    body.tr[htm.td[htm.code(".atr-word-wrap")[path]], [htm.td(".text-danger")[cell] for cell in cells]]
    if total > len(rows):
        block.p(".text-muted")[f"{total - len(rows):,} more not shown."]


async def _published_file(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, rel_path: str
) -> tuple[safe.RevisionNumber, atr.models.attestable.Attestable, int]:
    revision = await attestable.latest_checked_revision_number(project_key, version_key)
    attested = await attestable.load(project_key, version_key, revision) if (revision is not None) else None
    content_hash = attestable.path_hash(attested, rel_path) if (attested is not None) else None
    if (revision is None) or (attested is None) or (content_hash is None):
        raise base.ASFQuartException(errors.FILE_NOT_FOUND, errorcode=404)
    return revision, attested, attested.hashes[content_hash].size


def _reconciliation(
    primary_results: list[sql.CheckResult], member_counts: collections.Counter[sql.CheckResultStatus]
) -> str:
    primary_counts = collections.Counter(result.status for result in primary_results)
    sentences: list[str] = []
    for status in (
        sql.CheckResultStatus.BLOCKER,
        sql.CheckResultStatus.EXCEPTION,
        sql.CheckResultStatus.CONCERN,
        sql.CheckResultStatus.SUGGESTION,
    ):
        member_count = member_counts[status]
        if member_count == 0:
            continue
        primary_count = primary_counts[status]
        total = util.plural(primary_count + member_count, status.value)
        sentences.append(f"{total}: {primary_count} on this file, {member_count} on files inside this archive.")
    return " ".join(sentences)
