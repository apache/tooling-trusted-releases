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

from typing import Literal

import atr.blueprints.get as get
import atr.form as form
import atr.get.compose as compose
import atr.get.finish as finish
import atr.get.vote as vote
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.paths as paths
import atr.render as render
import atr.template as template
import atr.util as util
import atr.web as web

type Phase = Literal["COMPOSE", "VOTE", "FINISH"]


@get.typed
async def selected(
    session: web.Committer,
    _file: Literal["file"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
) -> str:
    """
    URL: /file/<project_name>/<version_name>
    View all the files in a release (any phase).
    """
    release = await session.release(project_name, version_name, phase=None)

    revision_number = release.latest_revision_number
    file_stats = []
    if release.phase == sql.ReleasePhase.RELEASE:
        file_stats = [
            stat async for stat in util.content_list(paths.get_finished_dir(), str(project_name), str(version_name))
        ]
    elif revision_number is not None:
        file_stats = [
            stat
            async for stat in util.content_list(
                paths.get_unfinished_dir(), str(project_name), str(version_name), revision_number
            )
        ]
    else:
        raise ValueError("No revision number found for unfinished release")
    file_stats.sort(key=lambda fs: fs.path)

    block = htm.Block()

    nav_info = _get_navigation_info(release)
    if nav_info:
        back_url, back_label, phase_label = nav_info
        render.html_nav(block, back_url, back_label, phase_label)

    block.h1["Files in ", htm.strong[release.project.short_display_name], " ", htm.em[release.version]]

    block.div(".card.mb-4")[
        htm.div(".card-header.d-flex.justify-content-between.align-items-center")[
            htm.h3(".mb-0")["Release information"]
        ],
        htm.div(".card-body")[
            htm.div(".row")[
                htm.div(".col-md-6")[
                    htm.p[htm.strong["Project:"], " ", release.project.display_name],
                    htm.p[htm.strong["Label:"], " ", release.name],
                ],
                htm.div(".col-md-6")[htm.p[htm.strong["Created:"], " ", release.created.strftime("%Y-%m-%d %H:%M:%S")]],
            ]
        ],
    ]

    files_card = htm.Block(htm.div, classes=".card.mb-4")
    files_card.div(".card-header.d-flex.justify-content-between.align-items-center")[htm.h3(".mb-0")["Files"]]

    if file_stats:
        tbody = htm.Block(htm.tbody)
        for stat in file_stats:
            if stat.is_file:
                file_url = util.as_url(
                    selected_path,
                    project_name=release.project.name,
                    version_name=release.version,
                    file_path=stat.path,
                )
                file_link = htm.a(href=file_url)[stat.path]
            else:
                file_link = htm.strong[stat.path + "/"]

            tbody.tr[
                htm.td[util.format_permissions(stat.permissions)],
                htm.td[file_link],
                htm.td[util.format_file_size(stat.size) if stat.is_file else "-"],
                htm.td[util.format_datetime(stat.modified)],
            ]

        files_card.div(".card-body")[
            htm.div(".table-responsive")[
                htm.table(".table.table-striped")[
                    htm.thead[
                        htm.tr[
                            htm.th["Permissions"],
                            htm.th["File path"],
                            htm.th["Size"],
                            htm.th["Modified"],
                        ]
                    ],
                    tbody.collect(),
                ]
            ]
        ]
    else:
        phase_name = _phase_display_name(release.phase)
        files_card.div(".card-body")[htm.div(".alert.alert-info")[f"This {phase_name} does not have any files."]]

    block.append(files_card.collect())

    return await template.blank(f"Files in {release.short_display_name}", content=block.collect())


@get.typed
async def selected_path(
    session: web.Committer,
    _file: Literal["file"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
    file_path: unsafe.Path,
) -> str:
    """
    URL: /file/<project_name>/<version_name>/<path:file_path>
    View the content of a specific file in a release (any phase).
    """
    validated_path = form.to_relpath(str(file_path))
    if validated_path is None:
        raise web.FlashError("Invalid file path")

    release = await session.release(project_name, version_name, phase=None)
    _max_view_size = 512 * 1024
    full_path = paths.release_directory(release) / validated_path
    content_listing = await util.archive_listing(full_path)
    content, is_text, is_truncated, error_message = await util.read_file_for_viewer(full_path, _max_view_size)

    block = htm.Block()

    back_url = util.as_url(selected, project_name=release.project.name, version_name=release.version)
    phase_name = _phase_display_name(release.phase)
    block.a(href=back_url, class_="atr-back-link")[f"← Back to {phase_name} files"]

    block.div(".p-3.mt-4.mb-4.bg-light.border.rounded")[
        htm.h2(".mt-0")[f"Viewing file: {validated_path}"],
        htm.p(".mb-0")[htm.strong["Release:"], " ", release.name],
    ]

    if content_listing:
        items = [htm.li(".list-group-item.py-1.px-3.small")[item] for item in content_listing]
        block.div(".card.mb-3")[
            htm.div(".card-header")[htm.h3(".mb-0")[f"Archive contents ({len(content_listing)})"]],
            htm.div(".card-body.p-0")[htm.ul(".list-group.list-group-flush")[*items]],
        ]

    if error_message:
        block.div(".alert.alert-danger")[error_message]
    elif content is not None:
        if content_listing:
            details_block = htm.Block(htm.details, classes=".mb-3")
            details_block.summary(".mb-2")["View raw file content"]
            _render_file_content(details_block, content, is_text, is_truncated, _max_view_size)
            block.append(details_block.collect())
        else:
            _render_file_content(block, content, is_text, is_truncated, _max_view_size)
    else:
        block.div(".alert.alert-secondary")["No content available for this file."]

    return await template.blank(
        f"View {release.project.short_display_name}/{release.version}/{validated_path}", content=block.collect()
    )


def _get_navigation_info(release: sql.Release) -> tuple[str, str, Phase] | None:
    """Get back URL, back label, and phase label based on release phase."""
    if release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
        return (
            util.as_url(compose.selected, project_name=release.project.name, version_name=release.version),
            f"Compose {release.short_display_name}",
            "COMPOSE",
        )
    elif release.phase == sql.ReleasePhase.RELEASE_CANDIDATE:
        return (
            util.as_url(vote.selected, project_name=release.project.name, version_name=release.version),
            f"Vote on {release.short_display_name}",
            "VOTE",
        )
    elif release.phase == sql.ReleasePhase.RELEASE_PREVIEW:
        return (
            util.as_url(finish.selected, project_name=release.project.name, version_name=release.version),
            f"Finish {release.short_display_name}",
            "FINISH",
        )
    return None


def _phase_display_name(phase: sql.ReleasePhase) -> str:
    """Get a display name for the phase."""
    if phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
        return "draft"
    elif phase == sql.ReleasePhase.RELEASE_CANDIDATE:
        return "candidate"
    elif phase == sql.ReleasePhase.RELEASE_PREVIEW:
        return "preview"
    elif phase == sql.ReleasePhase.RELEASE:
        return "release"
    return "release"


def _render_file_content(block: htm.Block, content: str, is_text: bool, is_truncated: bool, max_view_size: int) -> None:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header")[htm.h3(".mb-0")["File content" + (" (Hexdump)" if (not is_text) else "")]]

    if is_text:
        card.div(".card-body.p-0")[htm.pre(".bg-light.p-4.rounded-bottom.mb-0.text-break")[content]]
    else:
        card.div(".card-body.p-0")[htm.pre(".bg-light.p-4.rounded-bottom.mb-0.text-break")[htm.code[content]]]

    if is_truncated:
        card.div(".card-footer.text-muted.small")[
            f"Note: File content truncated to the first {util.format_file_size(max_view_size)}."
        ]

    block.append(card.collect())
