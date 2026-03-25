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
from typing import TYPE_CHECKING

import htpy

import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get as get
import atr.htm as htm
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.post as post
import atr.shared.draft as draft
import atr.storage as storage
import atr.storage.types as types
import atr.template as template
import atr.util as util
import atr.web as web

if TYPE_CHECKING:
    from collections.abc import Sequence


async def check(
    session: web.Committer | None,
    release: sql.Release,
    task_mid: str | None = None,
    vote_form: htm.Element | None = None,
    resolve_form: htm.Element | None = None,
    archive_url: str | None = None,
    vote_task: sql.Task | None = None,
    can_vote: bool = False,
    can_resolve: bool = False,
) -> web.WerkzeugResponse | str:
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

    if session is not None:
        asf_id = session.uid
        server_domain = session.app_host.split(":", 1)[0]
        server_host = session.app_host
        async with db.session() as data:
            user_ssh_keys = await data.ssh_key(asf_uid=session.uid).all()

    async with db.session() as data:
        quarantined_pending = await data.quarantined(release_key=release.key, status=sql.QuarantineStatus.PENDING).all()
        quarantined_failed = await data.quarantined(release_key=release.key, status=sql.QuarantineStatus.FAILED).all()

    clear_quarantine_forms: dict[int, htm.Element] = {}
    if session is not None:
        for q in quarantined_failed:
            if q.id is None:
                continue
            clear_quarantine_forms[q.id] = form.render(
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

    delete_form = form.render(
        model_cls=form.Empty,
        action=util.as_url(
            get.compose.selected, project_key=release.safe_project_key, version_key=release.safe_version_key
        ),
        submit_label="Delete this draft",
        submit_classes="btn btn-danger",
        empty=True,
        confirm="Are you sure you want to delete this draft? This cannot be undone.",
    )

    delete_file_forms: dict[str, htm.Element] = {}
    for path in all_paths:
        delete_file_forms[str(path)] = form.render(
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

    recheck_form = form.render(
        model_cls=form.Empty,
        action=util.as_url(
            post.draft.recheck, project_key=release.safe_project_key, version_key=release.safe_version_key
        ),
        submit_label="Recheck with fresh cache",
        submit_classes="btn btn-primary",
    )
    cache_reset_form = form.render(
        model_cls=form.Empty,
        action=util.as_url(
            post.draft.cache_reset, project_key=release.safe_project_key, version_key=release.safe_version_key
        ),
        submit_label="Recheck with global cache",
        submit_classes="btn btn-primary",
        submit_disabled=release.check_cache_key is None,
    )

    vote_task_warnings = _warnings_from_vote_result(vote_task)
    has_files = await util.has_files(release)

    blocker_errors = False
    if revision_number is not None:
        blocker_errors = await interaction.has_blocker_checks(release, revision_number)

    checks_summary_html = render_checks_summary(info, release.safe_project_key, release.safe_version_key)

    return await template.render(
        "check-selected.html",
        project_key=release.project.key,
        version_key=release.version,
        release=release,
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
        task_mid=task_mid,
        vote_form=vote_form,
        vote_task=vote_task,
        archive_url=archive_url,
        vote_task_warnings=vote_task_warnings,
        recheck_form=recheck_form,
        cache_reset_form=cache_reset_form,
        csrf_input=str(form.csrf_input()),
        resolve_form=resolve_form,
        has_files=has_files,
        blocker_errors=blocker_errors,
        can_vote=can_vote,
        can_resolve=can_resolve,
        checks_summary_html=checks_summary_html,
    )


def render_checks_summary(
    info: types.PathInfo | None, project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> htm.Element | None:
    if (info is None) or (not info.checker_stats):
        return None

    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header")[htpy.h5(".mb-0")["Checks summary"]]

    body = htm.Block(htm.div, classes=".card-body")
    for i, stat in enumerate(info.checker_stats):
        stripe_class = ".atr-stripe-odd" if ((i % 2) == 0) else ".atr-stripe-even"
        details = htm.Block(htm.details, classes=f".mb-0.p-2{stripe_class}")

        summary_content: list[htm.Element | str] = []
        if stat.warning_count > 0:
            summary_content.append(htpy.span(".badge.bg-warning.text-dark.me-2")[str(stat.warning_count)])
        if stat.failure_count > 0:
            summary_content.append(htpy.span(".badge.bg-danger.me-2")[str(stat.failure_count)])
        if stat.blocker_count > 0:
            summary_content.append(htpy.span(".badge.atr-bg-blocker.me-2")[str(stat.blocker_count)])
        summary_content.append(htpy.strong[_checker_display_name(stat.checker)])

        details.summary[*summary_content]

        files_div = htm.Block(htm.div, classes=".mt-2.atr-checks-files")
        all_files = set(stat.failure_files.keys()) | set(stat.warning_files.keys()) | set(stat.blocker_files.keys())
        for file_path in sorted(all_files):
            report_url = f"/report/{project_key!s}/{version_key!s}/{file_path}"
            error_count = stat.failure_files.get(file_path, 0)
            blocker_count = stat.blocker_files.get(file_path, 0)
            warning_count = stat.warning_files.get(file_path, 0)

            file_content: list[htm.Element | str] = []
            if error_count > 0:
                file_content.append(htpy.span(".badge.bg-danger.me-2")[util.plural(error_count, "error")])
            if blocker_count > 0:
                file_content.append(htpy.span(".badge.atr-bg-blocker.me-2")[util.plural(blocker_count, "blocker")])
            if warning_count > 0:
                file_content.append(
                    htpy.span(".badge.bg-warning.text-dark.me-2")[util.plural(warning_count, "warning")]
                )
            file_content.append(htpy.a(href=report_url)[htpy.strong[htpy.code[file_path]]])

            files_div.div[*file_content]

        details.append(files_div.collect())
        body.append(details.collect())

    card.append(body.collect())
    return card.collect()


def _checker_display_name(checker: str) -> str:
    return checker.removeprefix("atr.tasks.checks.").replace("_", " ").replace(".", " ").title()


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
