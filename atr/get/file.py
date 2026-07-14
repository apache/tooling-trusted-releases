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

from typing import Final, Literal

import atr.archives as archives
import atr.blueprints.get as get
import atr.cycles as cycles
import atr.db as db
import atr.form as form
import atr.get.compose as compose
import atr.get.finish as finish
import atr.get.vote as vote
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web

type Phase = Literal["COMPOSE", "VOTE", "FINISH"]

# The colour each phase already carries on its project page button
_PHASE_BADGE_VARIANTS: Final[dict[sql.ReleasePhase, str]] = {
    sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT: "secondary",
    sql.ReleasePhase.RELEASE_CANDIDATE: "info",
    sql.ReleasePhase.RELEASE_PREVIEW: "warning",
    sql.ReleasePhase.RELEASE: "success",
}


@get.typed
async def selected(
    session: web.Committer,
    _file: Literal["file"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /file/<project_key>/<version_key>
    View all the files in a release (any phase).
    """
    release = await session.release(project_key, version_key, phase=None)
    file_stats = await _release_file_stats(release, project_key, version_key)
    approval = await _archival_approval(release)

    block = htm.Block()

    nav_info = _get_navigation_info(release)
    if nav_info:
        back_url, back_label, phase_label = nav_info
        render.html_nav(block, back_url, back_label, phase_label)
    elif release.phase == sql.ReleasePhase.RELEASE:
        # Lazy import - projects imports this module, so a top level import would cycle
        import atr.get.projects as projects

        back_url = util.as_url(projects.view, project_key=release.project.key)
        block.a(".atr-back-link", href=back_url)[f"← Back to {release.project.short_display_name}"]

    block.h1["Files in ", htm.strong[release.project.short_display_name], " ", htm.em[release.version]]

    block.div(".card.mb-4")[
        htm.div(".card-header.d-flex.justify-content-between.align-items-center")[
            htm.h3(".mb-0")["Release information"]
        ],
        htm.div(".card-body")[
            htm.div(".row")[
                htm.div(".col-md-6")[
                    htm.p[htm.strong["Project:"], " ", release.project.display_name],
                    htm.p[htm.strong["Label:"], " ", release.key],
                ],
                htm.div(".col-md-6")[
                    htm.p[htm.strong["Created:"], " ", release.created.strftime("%Y-%m-%d %H:%M:%S")],
                    htm.p[htm.strong["Status:"], " ", _release_status(release, approval)],
                ],
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
                    project_key=release.project.key,
                    version_key=release.version,
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

    if release.phase == sql.ReleasePhase.RELEASE:
        actions_card = await _render_release_actions(session, release, approval)
        if actions_card is not None:
            block.append(actions_card)

    return await template.blank(f"Files in {release.short_display_name}", content=block.collect())


@get.typed
async def selected_path(
    session: web.Committer,
    _file: Literal["file"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_path: safe.RelPath,
) -> str:
    """
    URL: /file/<project_key>/<version_key>/<path:file_path>
    View the content of a specific file in a release (any phase).
    """

    release = await session.release(project_key, version_key, phase=None)
    _max_view_size = 512 * 1024
    full_path = paths.release_directory(release) / file_path
    content_listing = await archives.list_archive(full_path)
    content, is_text, is_truncated, error_message = await util.read_file_for_viewer(full_path, _max_view_size)

    block = htm.Block()

    back_url = util.as_url(selected, project_key=release.project.key, version_key=release.version)
    phase_name = _phase_display_name(release.phase)
    block.a(href=back_url, class_="atr-back-link")[f"← Back to {phase_name} files"]

    block.div(".p-3.mt-4.mb-4.bg-light.border.rounded")[
        htm.h2(".mt-0")[f"Viewing file: {file_path}"],
        htm.p(".mb-0")[htm.strong["Release:"], " ", release.key],
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
        f"View {release.project.short_display_name}/{release.version}/{file_path}", content=block.collect()
    )


async def _archival_approval(release: sql.Release) -> sql.ApprovalRequest | None:
    # Only a full release can be under an archival vote
    if release.phase != sql.ReleasePhase.RELEASE:
        return None
    async with db.session() as data:
        return await data.approval_request(
            project_key=str(release.project.key),
            status_in=[sql.ApprovalStatus.PENDING, sql.ApprovalStatus.APPROVED],
            release_version=release.version,
        ).get()


def _get_navigation_info(release: sql.Release) -> tuple[str, str, Phase] | None:
    """Get back URL, back label, and phase label based on release phase."""
    if release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
        return (
            util.as_url(compose.selected, project_key=release.project.key, version_key=release.version),
            f"Compose {release.short_display_name}",
            "COMPOSE",
        )
    elif release.phase == sql.ReleasePhase.RELEASE_CANDIDATE:
        return (
            util.as_url(vote.selected, project_key=release.project.key, version_key=release.version),
            f"Vote on {release.short_display_name}",
            "VOTE",
        )
    elif release.phase == sql.ReleasePhase.RELEASE_PREVIEW:
        return (
            util.as_url(finish.selected, project_key=release.project.key, version_key=release.version),
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


async def _release_file_stats(
    release: sql.Release,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> list[util.FileStat]:
    if release.phase == sql.ReleasePhase.RELEASE:
        gen = util.content_list(paths.get_finished_dir(), project_key, version_key)
    else:
        revision_number = release.safe_latest_revision_number
        gen = util.content_list(paths.get_unfinished_dir(), project_key, version_key, revision_number)
    file_stats = [stat async for stat in gen]
    file_stats.sort(key=lambda fs: fs.path)
    return file_stats


def _release_status(release: sql.Release, approval: sql.ApprovalRequest | None) -> htm.Element:
    if release.is_archived:
        archived_on = f" on {release.archived.strftime('%Y-%m-%d')}" if release.archived else ""
        return htm.span(".badge.text-bg-secondary")[f"Archived{archived_on}"]
    if approval is not None:
        if approval.status == sql.ApprovalStatus.PENDING:
            return htm.span(".badge.text-bg-secondary")["Archival vote in progress"]
        return htm.span(".badge.text-bg-warning")["Archival approved"]
    if release.phase == sql.ReleasePhase.RELEASE:
        label = "Released"
    else:
        label = _phase_display_name(release.phase).capitalize()
    return htm.span(f".badge.text-bg-{_PHASE_BADGE_VARIANTS[release.phase]}")[label]


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


async def _render_release_actions(
    session: web.Committer, release: sql.Release, approval: sql.ApprovalRequest | None
) -> htm.Element | None:
    project = release.project
    is_committee_member = bool(project.committee and user.is_committee_member(project.committee, session.uid))
    if not (is_committee_member or session.is_admin):
        return None

    async with db.session() as data:
        full_releases = await data.release(project_key=str(project.key), phase=sql.ReleasePhase.RELEASE).all()
    active_releases = [r for r in full_releases if not r.is_archived]

    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-0")["Release actions"]]
    body = htm.Block(htm.div, classes=".card-body")

    if release.is_archived:
        archived_on = f" on {release.archived.strftime('%Y-%m-%d')}" if release.archived else ""
        body.p(".text-muted.mb-0")[f"This release was archived{archived_on}."]
    elif approval is not None:
        if approval.status == sql.ApprovalStatus.PENDING:
            body.p(".mb-0")[
                f"An archival vote for this release is in progress (CAP #{approval.cap_question_id}, closes "
                f"{approval.closes_at.strftime('%Y-%m-%d %H:%M UTC')})."
            ]
        else:
            body.p[
                f"The archival vote passed (CAP #{approval.cap_question_id})."
                " Completing it archives this release and removes its files from the downloads area."
            ]
            body.append(
                await form.render(
                    model_cls=shared.projects.CompleteApprovalRequest,
                    action=util.as_url(post.projects.complete_approval),
                    form_classes=".d-inline-block.m-0",
                    submit_classes="btn-sm btn-outline-danger",
                    submit_label="Complete archival",
                    empty=True,
                    defaults={"approval_request_id": approval.id},
                )
            )
    else:
        latest = cycles.latest_release_in_cycle(project, release.version, active_releases)
        if (latest is None) or (latest.key == release.key):
            body.p[
                "This is the latest full release in its cycle, so archiving it requires a CAP approval vote"
                " by the committee PMC."
            ]
            body.append(
                await form.render(
                    model_cls=shared.projects.ArchiveSelectedRelease,
                    action=util.as_url(post.projects.archive_release),
                    form_classes=".d-inline-block.m-0",
                    submit_classes="btn-sm btn-outline-danger",
                    submit_label="Request archival vote",
                    empty=True,
                    defaults={"project_key": str(project.key), "release_version": release.version},
                    confirm=(
                        "This starts a binding CAP approval vote by the committee PMC. ATR will mark the"
                        " release ready to archive once the vote passes."
                    ),
                )
            )
        else:
            body.p["Archiving this release removes its files from the downloads area."]
            body.append(
                await form.render(
                    model_cls=shared.projects.ConfirmReleaseArchival,
                    action=util.as_url(post.projects.archive_release_confirm),
                    submit_classes="btn-sm btn-outline-danger",
                    submit_label="Archive release",
                    empty=True,
                    defaults={"project_key": str(project.key), "release_version": release.version},
                )
            )

    card.append(body.collect())
    return card.collect()
