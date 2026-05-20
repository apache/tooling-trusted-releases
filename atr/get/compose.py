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
import datetime
import pathlib
from collections.abc import Sequence
from typing import Any, Final, Literal

import aiofiles.os
import asfquart.base as base
import htpy
import quart
from quart_wtf import utils

import atr.blueprints.get as get
import atr.db as db
import atr.db.interaction as interaction
import atr.errors as errors
import atr.form as form
import atr.htm as htm
import atr.log as log
import atr.mapping as mapping
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.post as post
import atr.render as render
import atr.shared.draft as draft
import atr.storage as storage
import atr.template as template
import atr.util as util
import atr.web as web

_EMPTY_FILES_TABLE_HTML: Final[str] = '<div class="alert alert-info">This draft does not have any files yet.</div>'


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

    quarantined_pending, quarantined_failed = await _quarantine_alerts(release)
    clear_quarantine_forms = await _clear_quarantine_forms(release, quarantined_failed)

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
        action=util.as_url(
            post.draft.delete, project_key=release.safe_project_key, version_key=release.safe_version_key
        ),
        submit_label="Delete this draft",
        submit_classes="btn btn-danger",
        empty=True,
        confirm="Are you sure you want to delete this draft? This cannot be undone.",
    )

    delete_file_forms: dict[str, htm.Element] = {}
    for path in all_paths:
        delete_file_forms[str(path)] = await _render_delete_file_form(release, path)

    exception_banner_html: str = ""
    if info is not None:
        banner = render.render_exception_banner(info)
        if banner is not None:
            exception_banner_html = str(banner)
    files_table_html = await _render_files_table_html(
        release,
        paths=all_paths,
        info=info,
        project_key=release.safe_project_key,
        version_key=release.safe_version_key,
        delete_file_forms=delete_file_forms,
        exception_banner_html=exception_banner_html,
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

    polling_active = _compose_polling_active(ongoing_tasks_count, len(quarantined_pending))
    banner_html = _banner_html(len(quarantined_pending), ongoing_tasks_count)
    phase_value = release.phase.value
    release_info_html = await _render_release_info_html(
        release,
        phase=phase_value,
        revision_number=revision_number,
        revision_editor=revision_editor,
        revision_time=revision_timestamp,
        project_key=release.safe_project_key,
        version_key=release.safe_version_key,
        has_files=has_files,
        blocker_errors=blocker_errors,
        verification_pending=polling_active,
    )
    files_card_header_html = _files_card_header_html(phase_value, revision_number)

    checks_summary_html = render.render_checks_summary(info, release.safe_project_key, release.safe_version_key)
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

    archived_banner = render.archived_project_banner(release.project, "Release actions are disabled.")
    archived_banner_html = str(archived_banner) if archived_banner is not None else ""

    return await template.render(
        "check-selected.html",
        project_key=release.project.key,
        version_key=release.version,
        release=release,
        archived_banner_html=archived_banner_html,
        release_vote_mode=release.effective_vote_mode,
        paths=all_paths,
        info=info,
        revision_editor=revision_editor,
        revision_time=revision_timestamp,
        revision_number=revision_number,
        ongoing_tasks_count=ongoing_tasks_count,
        polling_active=polling_active,
        banner_html=banner_html,
        release_info_html=release_info_html,
        files_card_header_html=files_card_header_html,
        files_table_html=files_table_html,
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
        exception_banner_html=exception_banner_html,
        move_file_html=move_file_html,
        scripts=str(scripts),
    )


@get.typed
async def status_selected(
    session: web.Committer,
    _compose: Literal["compose"],
    _status: Literal["status"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.QuartResponse:
    """URL: /compose/status/<project_key>/<version_key>. Compose polling state JSON."""
    try:
        return await _status_selected_impl(session, project_key, version_key)
    except base.ASFQuartException as exc:
        return _status_error_response(exc, expose_message=True)
    except Exception:
        log.exception(f"status_selected failed for {project_key}/{version_key}")
        return _status_error_response(None, default_status=500, expose_message=False)


def _banner_html(quarantine_pending: int, ongoing: int) -> str:
    is_are = "is" if (ongoing == 1) else "are"
    task_word = "task" if (ongoing == 1) else "tasks"
    count = htpy.strong(id="ongoing-tasks-count")[str(ongoing)]
    if (quarantine_pending > 0) and (ongoing > 0):
        return str(
            htpy.fragment[
                "Archive validation is in progress and there ",
                is_are,
                " currently ",
                count,
                f" background verification {task_word} running for the latest revision."
                " Results shown below may be incomplete or outdated until they finish.",
            ]
        )
    if quarantine_pending > 0:
        hidden_count = htpy.strong(".d-none", id="ongoing-tasks-count")[str(ongoing)]
        return str(
            htpy.fragment[
                "Archive validation is in progress. The page will refresh automatically when validation completes.",
                hidden_count,
            ]
        )
    return str(
        htpy.fragment[
            f"There {is_are} currently ",
            count,
            f" background verification {task_word} running for the latest revision."
            " Results shown below may be incomplete or outdated until the tasks finish.",
        ]
    )


async def _clear_quarantine_forms(
    release: sql.Release, quarantined_failed: list[sql.Quarantined]
) -> dict[int, htm.Element]:
    forms: dict[int, htm.Element] = {}
    for q in quarantined_failed:
        if q.id is None:
            continue
        forms[q.id] = await form.render(
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
    return forms


def _compose_polling_active(ongoing: int, quarantine_pending: int) -> bool:
    return (ongoing > 0) or (quarantine_pending > 0)


def _files_card_header_html(phase: str, revision_number: safe.RevisionNumber | None) -> str:
    if revision_number is None:
        return "Files"
    if phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT.value:
        suffix = f" in revision {revision_number}"
    else:
        suffix = f" in the release candidate (revision {revision_number})"
    return str(htpy.fragment["Files", suffix])


async def _quarantine_alerts(
    release: sql.Release,
) -> tuple[list[sql.Quarantined], list[sql.Quarantined]]:
    # Fetch STAGING, PENDING, and FAILED in one query
    async with db.session() as data:
        rows = await data.quarantined(
            release_key=release.key,
            status_in=[
                sql.QuarantineStatus.STAGING,
                sql.QuarantineStatus.PENDING,
                sql.QuarantineStatus.FAILED,
            ],
        ).all()
    pending: list[sql.Quarantined] = []
    failed: list[sql.Quarantined] = []
    for row in rows:
        if row.status == sql.QuarantineStatus.FAILED:
            failed.append(row)
        else:
            pending.append(row)
    return pending, failed


async def _render_delete_file_form(release: sql.Release, path: pathlib.Path | safe.RelPath) -> htm.Element:
    return await form.render(
        model_cls=draft.DeleteFileForm,
        action=util.as_url(
            post.draft.delete_file,
            project_key=release.safe_project_key,
            version_key=release.safe_version_key,
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


async def _render_files_table_html(
    release: sql.Release,
    *,
    paths: Sequence[pathlib.Path | safe.RelPath],
    info: Any,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    delete_file_forms: dict[str, htm.Element],
    exception_banner_html: str,
) -> str:
    if not paths:
        return _EMPTY_FILES_TABLE_HTML
    return await template.render(
        "check-selected-path-table.html",
        paths=paths,
        info=info,
        project_key=str(project_key),
        version_key=str(version_key),
        release=release,
        phase=release.phase.value,
        delete_file_forms=delete_file_forms,
        csrf_input=str(form.csrf_input()),
        exception_banner_html=exception_banner_html,
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


async def _render_release_info_html(
    release: sql.Release,
    *,
    phase: str,
    revision_number: safe.RevisionNumber | None,
    revision_editor: str | None,
    revision_time: datetime.datetime | None,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    has_files: bool,
    blocker_errors: bool,
    verification_pending: bool,
) -> str:
    return await template.render(
        "check-selected-release-info.html",
        release=release,
        phase=phase,
        release_vote_mode=release.effective_vote_mode,
        revision_number=revision_number,
        revision_editor=revision_editor,
        revision_time=revision_time,
        project_key=project_key,
        version_key=version_key,
        has_files=has_files,
        blocker_errors=blocker_errors,
        verification_pending=verification_pending,
        format_datetime=util.format_datetime,
    )


def _serialise_revision_number(revision_number: safe.RevisionNumber | str | None) -> str | None:
    return str(revision_number) if (revision_number is not None) else None


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


def _status_error_response(
    exc: BaseException | None, *, default_status: int = 500, expose_message: bool = False
) -> web.QuartResponse:
    if exc is not None:
        status = errors.response_status_code(exc, default=default_status)
    else:
        status = default_status
    if expose_message and (exc is not None):
        error_text = errors.message(exc)
    else:
        error_text = "Compose status temporarily unavailable"
    payload: dict[str, Any] = {
        "polling_active": False,
        "ongoing": 0,
        "quarantine_pending": 0,
        "quarantine_failed": 0,
        "latest_revision_number": None,
        "vote_blocked": False,
        "redirect_url": None,
        "error": error_text,
    }
    response = quart.jsonify(payload)
    response.status_code = status
    return response


async def _status_selected_impl(
    session: web.Committer,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.QuartResponse:
    # Polled every few seconds, so do not flash the admin warning
    await session.prevent_confusing_ui_display(project_key, flash_admin_warning=False)
    async with db.session() as data:
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
            _release_policy=True,
            _project_release_policy=True,
        ).demand(base.ASFQuartException("Release does not exist", errorcode=404))

    if release.phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
        return quart.jsonify(
            {
                "polling_active": False,
                "ongoing": 0,
                "quarantine_pending": 0,
                "quarantine_failed": 0,
                "latest_revision_number": _serialise_revision_number(release.latest_revision_number),
                "vote_blocked": False,
                "redirect_url": mapping.release_as_url(release),
            }
        )

    quarantined_pending, quarantined_failed = await _quarantine_alerts(release)
    clear_quarantine_forms = await _clear_quarantine_forms(release, quarantined_failed)

    ongoing_tasks_count = 0
    revision_number: safe.RevisionNumber | None = None
    revision_editor: str | None = None
    revision_time: datetime.datetime | None = None
    match await interaction.latest_info(release.safe_project_key, release.safe_version_key):
        case (latest_number, editor, timestamp):
            revision_number = latest_number
            revision_editor = editor
            revision_time = timestamp
            ongoing_tasks_count = await interaction.tasks_ongoing(
                release.safe_project_key,
                release.safe_version_key,
                latest_number,
            )

    base_path = paths.release_directory(release)
    all_paths = [path async for path in util.paths_recursive(base_path)]
    all_paths.sort()
    async with storage.read(session) as read:
        ragp = read.as_general_public()
        info = await ragp.releases.path_info(release, all_paths)

    exception_banner_html = ""
    if info is not None:
        banner_elem = render.render_exception_banner(info)
        if banner_elem is not None:
            exception_banner_html = str(banner_elem)

    # Always rebuild the files table fragment
    # Check results can change between polls
    delete_file_forms: dict[str, htm.Element] = {}
    for path in all_paths:
        delete_file_forms[str(path)] = await _render_delete_file_form(release, path)
    files_table_html = await _render_files_table_html(
        release,
        paths=all_paths,
        info=info,
        project_key=release.safe_project_key,
        version_key=release.safe_version_key,
        delete_file_forms=delete_file_forms,
        exception_banner_html=exception_banner_html,
    )

    checks_summary_elem = render.render_checks_summary(info, release.safe_project_key, release.safe_version_key)
    checks_summary_html = str(checks_summary_elem) if checks_summary_elem else ""

    quarantine_html = await template.render(
        "check-selected-quarantine.html",
        quarantined_pending=quarantined_pending,
        quarantined_failed=quarantined_failed,
        clear_quarantine_forms=clear_quarantine_forms,
        format_datetime=util.format_datetime,
    )

    has_files = await util.has_files(release)
    vote_blocked = False
    if revision_number is not None:
        vote_blocked = await interaction.has_blocker_checks(release, revision_number)

    polling_active = _compose_polling_active(ongoing_tasks_count, len(quarantined_pending))
    phase_value = release.phase.value
    release_info_html = await _render_release_info_html(
        release,
        phase=phase_value,
        revision_number=revision_number,
        revision_editor=revision_editor,
        revision_time=revision_time,
        project_key=project_key,
        version_key=version_key,
        has_files=has_files,
        blocker_errors=vote_blocked,
        verification_pending=polling_active,
    )
    files_card_header_html = _files_card_header_html(phase_value, revision_number)

    payload: dict[str, Any] = {
        "polling_active": polling_active,
        "ongoing": ongoing_tasks_count,
        "quarantine_pending": len(quarantined_pending),
        "quarantine_failed": len(quarantined_failed),
        "latest_revision_number": _serialise_revision_number(revision_number),
        "vote_blocked": vote_blocked,
        "banner_html": _banner_html(len(quarantined_pending), ongoing_tasks_count),
        "quarantine_html": quarantine_html,
        "checks_summary_html": checks_summary_html,
        "files_table_html": files_table_html,
        "release_info_html": release_info_html,
        "files_card_header_html": files_card_header_html,
        "redirect_url": None,
    }
    return quart.jsonify(payload)


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
