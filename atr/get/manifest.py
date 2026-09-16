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

import dataclasses
from typing import Literal

import asfquart.base as base

import atr.attestable as attestable
import atr.blueprints.get as get
import atr.db as db
import atr.errors as errors
import atr.htm as htm
import atr.models.attestable as models
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.render as render
import atr.template as template
import atr.util as util
import atr.web as web


@dataclasses.dataclass
class ManifestQuery(web.PageQuery):
    limit: int = 250


@get.typed
async def selected(
    _session: web.Public,
    _manifest: Literal["manifest"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    query_args: ManifestQuery,
) -> str:
    try:
        validation.pagination_args_validate(query_args)
    except ValueError as e:
        raise base.ASFQuartException(str(e), errorcode=400)
    async with db.session() as data:
        release = await data.release(project_key=str(project_key), version=str(version_key)).demand(
            base.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404)
        )
    recorded = await attestable.load(project_key, version_key, revision_number)
    if recorded is None:
        raise base.ASFQuartException(errors.MANIFEST_NOT_FOUND, errorcode=404)
    return await template.blank(
        title=f"File manifest for {release.short_display_name}, ATR revision {revision_number}",
        content=_render_page(release, revision_number, recorded, query_args),
    )


def _page_url(release: sql.Release, revision_number: safe.RevisionNumber, limit: int, offset: int | None) -> str | None:
    if offset is None:
        return None
    return util.as_url(
        selected,
        project_key=release.project_key,
        version_key=release.version,
        revision_number=revision_number,
        limit=limit,
        offset=offset,
    )


def _render_page(
    release: sql.Release, revision_number: safe.RevisionNumber, recorded: models.Attestable, query_args: ManifestQuery
) -> htm.Element:
    path_hashes = attestable.path_hashes(recorded)
    entries = sorted(path_hashes.items())[query_args.offset : (query_args.offset + query_args.limit)]
    pagination = web.page_nav(query_args.offset, query_args.limit, len(path_hashes), len(entries))
    paged = (pagination.previous_offset is not None) or (pagination.next_offset is not None)
    page = htm.Block()
    page.h1["File manifest"]
    page.p[
        "Files recorded for ",
        htm.strong[release.project.display_name],
        " ",
        htm.em[release.version],
        ", ATR revision ",
        htm.code[str(revision_number)],
        ".",
    ]
    page.p[f"{util.plural(len(path_hashes), 'file')} recorded."]
    if not path_hashes:
        page.p(".text-muted")["This revision contains no files."]
        return page.collect()
    page.p[
        "Each content digest includes its hash algorithm, such as ",
        htm.code["blake3:"],
        ". Select a digest to copy it for comparison with a downloaded file.",
    ]
    missing_sizes = sum(content_hash not in recorded.hashes for content_hash in path_hashes.values())
    if missing_sizes:
        page.div(".alert.alert-warning")[f"Recorded sizes are unavailable for {util.plural(missing_sizes, 'file')}."]
    if paged and entries:
        page.p[f"Showing files {pagination.start} to {pagination.end} of {len(path_hashes)}."]
    if entries:
        _render_table(page, entries, recorded)
    else:
        page.p(".text-muted")["This page contains no files."]
    if paged:
        render.html_page_nav(
            page,
            aria_label="File manifest pages",
            previous_url=_page_url(release, revision_number, query_args.limit, pagination.previous_offset),
            next_url=_page_url(release, revision_number, query_args.limit, pagination.next_offset),
        )
    return page.collect()


def _render_table(page: htm.Block, entries: list[tuple[str, str]], recorded: models.Attestable) -> None:
    with page.block(
        htm.div(".table-responsive.atr-manifest-scroll", tabindex=0, role="region", aria_label="File manifest")
    ) as container:
        with container.block(htm.table, classes=".table.table-striped.atr-manifest-table") as table:
            table.thead[
                htm.tr[
                    htm.th(scope="col")["Path"],
                    htm.th(".text-end", scope="col")["Size (bytes)"],
                    htm.th(scope="col")["Content digest"],
                ]
            ]
            with table.block(htm.tbody) as body:
                for path, content_hash in entries:
                    entry = recorded.hashes.get(content_hash)
                    body.tr[
                        htm.td[htm.code(".atr-word-wrap")[path]],
                        htm.td(".text-end.text-nowrap")[str(entry.size) if (entry is not None) else "Unknown"],
                        htm.td[htm.code(".atr-word-wrap.user-select-all")[content_hash]],
                    ]
