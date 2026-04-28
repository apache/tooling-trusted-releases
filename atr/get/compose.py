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
from typing import TYPE_CHECKING, Literal

import aiofiles.os
import asfquart.base as base
import htpy
from quart_wtf import utils

import atr.blueprints.get as get
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.htm as htm
import atr.mapping as mapping
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.post as post
import atr.shared as shared
import atr.shared.draft as draft
import atr.storage as storage
import atr.template as template
import atr.util as util
import atr.web as web

if TYPE_CHECKING:
    from collections.abc import Sequence


@get.typed
async def selected(
    session: web.Committer,
    _compose: Literal["compose"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.WerkzeugResponse | str:
    """
    URL: /compose/<project_key>/<version_key>
    Show the contents of the release candidate draft.
    """
    await session.prevent_confusing_ui_display(project_key)
    async with db.session() as data:
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
            _release_policy=True,
            _project_release_policy=True,
        ).demand(base.ASFQuartException("Release does not exist", errorcode=404))
    if release.phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
        return await mapping.release_as_redirect(session, release)

    base_path = paths.release_directory(release)

    # TODO: This takes 180ms for providers
    # We could cache it
    all_paths = [path async for path in util.paths_recursive(base_path)]
    all_paths.sort()

    async with storage.read(session) as read:
        ragp = read.as_general_public()
        info = await ragp.releases.path_info(release, all_paths)

    user_ssh_keys: Sequence[sql.SSHKey] = []
    asf_id: str | None = None
    server_domain: str | None = None
    server_host: str | None = None

    asf_id = session.uid
    server_domain = session.app_host.split(":", 1)[0]
    server_host = session.app_host
    async with db.session() as data:
        user_ssh_keys = await data.ssh_key(asf_uid=session.uid).all()

    async with db.session() as data:
        quarantined_pending = await data.quarantined(release_key=release.key, status=sql.QuarantineStatus.PENDING).all()
        quarantined_failed = await data.quarantined(release_key=release.key, status=sql.QuarantineStatus.FAILED).all()

    clear_quarantine_forms: dict[int, htm.Element] = {}
    for q in quarantined_failed:
        if q.id is None:
            continue
        clear_quarantine_forms[q.id] = await form.render(
            model_cls=draft.ClearQuarantineForm,
            action=util.as_url(
                post.draft.quarantine_clear,
                project_key=release.safe_project_key,
                version_key=release.safe_version_key,
            ),
            form_classes=".d-inline-block.m-0",
            submit_classes="btn-sm btn-outline-secondary",
            submit_label="Dismiss",
            empty=True,
            defaults={"quarantined_id": str(q.id)},
        )

    # Get the number of ongoing tasks for the current revision
    ongoing_tasks_count = 0
    match await interaction.latest_info(release.safe_project_key, release.safe_version_key):
        case (revision_number, revision_editor, revision_timestamp):
            ongoing_tasks_count = await interaction.tasks_ongoing(
                release.safe_project_key,
                release.safe_version_key,
                revision_number,
            )
        case None:
            revision_number = None
            revision_editor = None
            revision_timestamp = None

    delete_form = await form.render(
        model_cls=form.Empty,
        action=util.as_url(selected, project_key=release.safe_project_key, version_key=release.safe_version_key),
        submit_label="Delete this draft",
        submit_classes="btn btn-danger",
        empty=True,
        confirm="Are you sure you want to delete this draft? This cannot be undone.",
    )

    delete_file_forms: dict[str, htm.Element] = {}
    for path in all_paths:
        delete_file_forms[str(path)] = await form.render(
            model_cls=draft.DeleteFileForm,
            action=util.as_url(
                post.draft.delete_file, project_key=release.safe_project_key, version_key=release.safe_version_key
            ),
            form_classes=".d-inline-block.m-0",
            submit_classes="btn-sm btn-outline-danger",
            submit_label="Delete",
            empty=True,
            defaults={"file_path": str(path)},
            # TODO: Add a static check for the confirm syntax
            confirm=(
                "Are you sure you want to delete this file? "
                "This will also delete any associated metadata files. "
                "This cannot be undone."
            ),
        )

    recheck_form = await form.render(
        model_cls=form.Empty,
        action=util.as_url(
            post.draft.recheck, project_key=release.safe_project_key, version_key=release.safe_version_key
        ),
        submit_label="Recheck with fresh cache",
        submit_classes="btn btn-primary",
    )
    cache_reset_form = await form.render(
        model_cls=form.Empty,
        action=util.as_url(
            post.draft.cache_reset, project_key=release.safe_project_key, version_key=release.safe_version_key
        ),
        submit_label="Recheck with global cache",
        submit_classes="btn btn-primary",
        submit_disabled=release.check_cache_key is None,
    )

    has_files = await util.has_files(release)

    blocker_errors = False
    if revision_number is not None:
        blocker_errors = await interaction.has_blocker_checks(release, revision_number)

    checks_summary_html = shared.web.render_checks_summary(info, release.safe_project_key, release.safe_version_key)
    move_file_html = _render_move_section(10)

    csrf_token = utils.generate_csrf()
    # Should be already validated, but check again
    latest_revision_dir = paths.release_directory(release)
    source_files_rel, target_dirs = await _sources_and_targets(latest_revision_dir)
    safe_source_files_rel = [util.validate_path(f).as_posix() for f in sorted(source_files_rel)]
    safe_target_dirs = [util.validate_path(d).as_posix() for d in sorted(target_dirs)]
    scripts = htpy.fragment[
        htpy.script(id="file-data", type="application/json")[util.json_for_script_element(safe_source_files_rel)],
        htpy.script(id="dir-data", type="application/json")[util.json_for_script_element(safe_target_dirs)],
        htpy.script(
            id="main-script-data",
            src=util.static_url("js/ts/move-files.js"),
            **{"data-csrf-token": csrf_token},
        )[""],
    ]

    return await template.render(
        "check-selected.html",
        project_key=release.project.key,
        version_key=release.version,
        release=release,
        release_vote_mode=release.effective_vote_mode,
        paths=all_paths,
        info=info,
        revision_editor=revision_editor,
        revision_time=revision_timestamp,
        revision_number=revision_number,
        ongoing_tasks_count=ongoing_tasks_count,
        quarantined_pending=quarantined_pending,
        quarantined_failed=quarantined_failed,
        clear_quarantine_forms=clear_quarantine_forms,
        delete_form=delete_form,
        delete_file_forms=delete_file_forms,
        asf_id=asf_id,
        server_domain=server_domain,
        server_host=server_host,
        user_ssh_keys=user_ssh_keys,
        format_datetime=util.format_datetime,
        models=sql,
        recheck_form=recheck_form,
        cache_reset_form=cache_reset_form,
        csrf_input=str(form.csrf_input()),
        has_files=has_files,
        blocker_errors=blocker_errors,
        checks_summary_html=checks_summary_html,
        move_file_html=move_file_html,
        scripts=str(scripts),
    )


def _render_move_section(max_files_to_show: int = 10) -> htm.Element:
    """Render the move files section with JavaScript interaction."""
    section = htm.Block()

    section.h2["Move items to a different directory"]
    section.p[
        """
        Move files in the compose area using the form below. You can change
        files freely until holding a vote, and then file locations are frozen.
        Moving files now only moves them on ATR, but as soon as a vote is
        started, if the vote is successful then the current locations will
        determine where they are published to SVN (when ATR supports this
        feature).
        """
        "Files with associated metadata (e.g. ",
        htm.code[".asc"],
        " or ",
        htm.code[".sha512"],
        " files) are treated as a single unit and will be moved together if any one of them is selected for movement.",
    ]

    section.append(htm.div("#move-error-alert.alert.alert-danger.d-none", role="alert", **{"aria-live": "assertive"}))

    left_card = htm.Block(htm.div, classes=".card.mb-4")
    left_card.div(".card-header.bg-light")[htm.h3(".mb-0")["Select items to move"]]
    left_card.div(".card-body")[
        htpy.input(
            "#file-filter.form-control.mb-2",
            type="text",
            placeholder="Search for an item to move...",
        ),
        htm.table(".table.table-sm.table-striped.border.mt-3")[htm.tbody("#file-list-table-body")],
        htm.div("#file-list-more-info.text-muted.small.mt-1"),
        htpy.button(
            "#select-files-toggle-button.btn.btn-outline-secondary.w-100.mt-2",
            type="button",
        )["Select these files"],
    ]

    right_card = htm.Block(htm.div, classes=".card.mb-4")
    right_card.div(".card-header.bg-light")[
        htm.h3(".mb-0")[
            htm.span("#selected-file-name-title")["Select a destination for the file"],
            htm.span(".text-muted.small")[" (enter a new directory name to create it)"],
        ]
    ]
    right_card.div(".card-body")[
        htpy.input(
            "#dir-filter-input.form-control.mb-2",
            type="text",
            placeholder="Search for a directory to move to...",
        ),
        htm.table(".table.table-sm.table-striped.border.mt-3")[htm.tbody("#dir-list-table-body")],
        htm.div("#dir-list-more-info.text-muted.small.mt-1"),
    ]

    section.form(".atr-canary")[
        htm.div(".row")[
            htm.div(".col-lg-6")[left_card.collect()],
            htm.div(".col-lg-6")[right_card.collect()],
        ],
        htm.div(".mb-3")[
            htpy.label(".form-label", for_="maxFilesInput")["Items to show per list:"],
            htpy.input(
                "#max-files-input.form-control.form-control-sm.w-25",
                type="number",
                value=str(max_files_to_show),
                min="1",
            ),
        ],
        htm.div("#current-move-selection-info.text-muted")["Please select a file and a destination."],
        htm.div[htpy.button("#confirm-move-button.btn.btn-success.mt-2", type="button")["Move to selected directory"]],
    ]

    return section.collect()


async def _sources_and_targets(latest_revision_dir: safe.StatePath) -> tuple[list[pathlib.Path], set[pathlib.Path]]:
    source_items_rel: list[pathlib.Path] = []
    target_dirs: set[pathlib.Path] = {pathlib.Path(".")}

    async for item_rel_path in util.paths_recursive_all(latest_revision_dir):
        current_parent = item_rel_path.parent
        source_items_rel.append(item_rel_path)

        while True:
            target_dirs.add(current_parent)
            if current_parent == pathlib.Path("."):
                break
            current_parent = current_parent.parent

        item_abs_path = latest_revision_dir / item_rel_path
        if await aiofiles.os.path.isfile(item_abs_path):
            pass
        elif await aiofiles.os.path.isdir(item_abs_path):
            target_dirs.add(item_rel_path)

    return source_items_rel, target_dirs


def _warnings_from_vote_result(vote_task: sql.Task | None) -> list[str]:
    # TODO: Replace this with a schema.Strict model
    # But we'd still need to do some of this parsing and validation
    # We should probably rethink how to send data through tasks

    if (not vote_task) or (not vote_task.result):
        return ["No vote task result found."]

    vote_task_result = vote_task.result
    if not isinstance(vote_task_result, results.VoteInitiate):
        return ["Vote task result is not a results.VoteInitiate instance."]

    return vote_task_result.mail_send_warnings
