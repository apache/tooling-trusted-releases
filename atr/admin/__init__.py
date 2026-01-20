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
import datetime
import os
import pathlib
import statistics
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any, Final, Literal, NamedTuple

import aiofiles.os
import aiohttp
import asfquart
import asfquart.base as base
import asfquart.session
import htpy
import ldap3.utils.conv as conv
import pydantic
import quart
import sqlalchemy.orm as orm
import sqlmodel

import atr.blueprints.admin as admin
import atr.config as config
import atr.datasources.apache as apache
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get as get
import atr.htm as htm
import atr.ldap as ldap
import atr.log as log
import atr.mapping as mapping
import atr.models.sql as sql
import atr.principal as principal
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.storage.types as types
import atr.tasks as tasks
import atr.template as template
import atr.util as util
import atr.validate as validate
import atr.web as web

ROUTES_MODULE: Final[Literal[True]] = True


class BrowseAsUserForm(form.Form):
    uid: str = form.label("ASF UID", "Enter the ASF UID to browse as.")


class DeleteCommitteeKeysForm(form.Form):
    committee_name: str = form.label("Committee", widget=form.Widget.SELECT)
    confirm_delete: Literal["DELETE KEYS"] = form.label("Confirmation", "Type DELETE KEYS to confirm.")


class DeleteReleaseForm(form.Form):
    releases_to_delete: form.StrList = form.label("Select releases to delete", widget=form.Widget.CUSTOM)
    confirm_delete: Literal["DELETE"] = form.label("Confirmation", "Type DELETE to confirm.")

    @pydantic.field_validator("releases_to_delete")
    @classmethod
    def validate_releases_to_delete(cls, v: form.StrList) -> form.StrList:
        if len(v) == 0:
            raise ValueError("You must select at least one release to delete")
        return v


class LdapLookupForm(form.Form):
    uid: str = form.label("ASF UID (optional)", "Enter ASF UID, e.g. johnsmith, or * for all")
    email: str = form.label("Email address (optional)", "Enter email address, e.g. user@example.org")


class SessionDataCommon(NamedTuple):
    uid: str
    fullname: str
    email: str
    is_member: bool
    is_chair: bool
    is_root: bool
    pmcs: list[str]
    projects: list[str]


@admin.get("/all-releases")
async def all_releases(session: web.Committer) -> str:
    """Display a list of all releases across all phases."""
    async with db.session() as data:
        releases = await data.release(_project=True, _committee=True).order_by(sql.Release.name).all()
    return await template.render("all-releases.html", releases=releases, release_as_url=mapping.release_as_url)


@admin.get("/browse-as")
async def browse_as_get(session: web.Committer) -> str | web.WerkzeugResponse:
    """Allows an admin to browse as another user."""
    rendered_form = form.render(
        model_cls=BrowseAsUserForm,
        submit_label="Browse as this user",
    )
    return await template.render("browse-as.html", form=rendered_form)


@admin.post("/browse-as")
@admin.form(BrowseAsUserForm)
async def browse_as_post(session: web.Committer, browse_form: BrowseAsUserForm) -> str | web.WerkzeugResponse:
    """Allows an admin to browse as another user."""
    # TODO: Enable this in debugging mode only?
    import atr.get.root as root

    new_uid = browse_form.uid
    if not (current_session := await asfquart.session.read()):
        raise base.ASFQuartException("Not authenticated", 401)

    bind_dn, bind_password = principal.get_ldap_bind_dn_and_password()
    ldap_params = ldap.SearchParameters(
        uid_query=new_uid,
        bind_dn_from_config=bind_dn,
        bind_password_from_config=bind_password,
    )
    await asyncio.to_thread(ldap.search, ldap_params)

    if not ldap_params.results_list:
        await quart.flash(f"User '{new_uid}' not found in LDAP.", "error")
        return await session.redirect(browse_as_get)

    ldap_projects_data = await apache.get_ldap_projects_data()
    committee_data = await apache.get_active_committee_data()
    ldap_data = ldap_params.results_list[0]
    log.info(f"Current ASFQuart session data: {current_session}")
    new_session_data = _session_data(
        ldap_data,
        new_uid,
        current_session,
        ldap_projects_data,
        committee_data,
        bind_dn,
        bind_password,
    )
    log_safe_data = _log_safe_session_data(
        ldap_data,
        new_uid,
        ldap_projects_data,
        committee_data,
        bind_dn,
        bind_password,
    )
    log.info(f"New Quart cookie (not ASFQuart session) data: {log_safe_data}")
    asfquart.session.write(new_session_data)

    await quart.flash(
        f"You are now browsing as '{new_uid}'. To return to your own account, please log out and log back in.",
        "success",
    )
    return await session.redirect(root.index)


@admin.get("/configuration")
async def configuration(session: web.Committer) -> web.QuartResponse:
    """Display the current application configuration values."""

    conf = config.get()
    values: list[str] = []
    for name in dir(conf):
        if name.startswith("_"):
            continue
        try:
            val = getattr(conf, name)
        except Exception as exc:
            val = log.python_repr(f"error: {exc}")
        if name.endswith("_PASSWORD"):
            val = log.python_repr("redacted")
        if callable(val):
            continue
        values.append(f"{name}={val}")

    values.sort()
    return web.TextResponse("\n".join(values))


@admin.get("/consistency")
async def consistency(session: web.Committer) -> web.TextResponse:
    """Check for consistency between the database and the filesystem."""
    # Get all releases from the database
    async with db.session() as data:
        releases = await data.release().all()
    database_dirs = []
    for release in releases:
        path = util.release_directory_version(release)
        database_dirs.append(str(path))
    if len(set(database_dirs)) != len(database_dirs):
        raise base.ASFQuartException("Duplicate release directories in database", errorcode=500)

    # Get all releases from the filesystem
    filesystem_dirs = await _get_filesystem_dirs()

    # Pair them up where possible
    paired_dirs = []
    for database_dir in database_dirs[:]:
        for filesystem_dir in filesystem_dirs[:]:
            if database_dir == filesystem_dir:
                paired_dirs.append(database_dir)
                database_dirs.remove(database_dir)
                filesystem_dirs.remove(filesystem_dir)
                break
    return web.TextResponse(
        "=== BROKEN ===\n"
        "\n"
        "DATABASE ONLY:\n"
        "\n"
        f"{chr(10).join(sorted(database_dirs or ['-']))}\n"
        "\n"
        "FILESYSTEM ONLY:\n"
        "\n"
        f"{chr(10).join(sorted(filesystem_dirs or ['-']))}\n"
        "\n"
        "\n"
        "== Okay ==\n"
        "\n"
        "Paired correctly:\n"
        "\n"
        f"{chr(10).join(sorted(paired_dirs or ['-']))}\n"
    )


@admin.get("/data")
async def data(session: web.Committer) -> str:
    return await _data(session, "Committee")


@admin.get("/data/<model>")
async def data_model(session: web.Committer, model: str = "Committee") -> str:
    return await _data(session, model)


@admin.get("/delete-committee-keys")
async def delete_committee_keys_get(session: web.Committer) -> str | web.WerkzeugResponse:
    """Display the form to delete committee keys."""
    async with db.session() as data:
        all_committees = await data.committee(_public_signing_keys=True).order_by(sql.Committee.name).all()
        committees_with_keys = [c for c in all_committees if c.public_signing_keys]

    committee_choices = [(c.name, c.display_name) for c in committees_with_keys]

    rendered_form = form.render(
        model_cls=DeleteCommitteeKeysForm,
        submit_label="Delete all keys for selected committee",
        defaults={"committee_name": committee_choices},
    )
    return await template.render("delete-committee-keys.html", form=rendered_form)


@admin.post("/delete-committee-keys")
@admin.form(DeleteCommitteeKeysForm)
async def delete_committee_keys_post(
    session: web.Committer, delete_form: DeleteCommitteeKeysForm
) -> str | web.WerkzeugResponse:
    """Delete all keys for selected committee."""
    committee_name = delete_form.committee_name

    async with db.session() as data:
        committee_query = data.committee(name=committee_name)
        via = sql.validate_instrumented_attribute
        committee_query.query = committee_query.query.options(
            orm.selectinload(via(sql.Committee.public_signing_keys)).selectinload(via(sql.PublicSigningKey.committees))
        )
        committee = await committee_query.get()

        if not committee:
            await quart.flash(f"Committee '{committee_name}' not found.", "error")
            return await session.redirect(delete_committee_keys_get)

        keys_to_check = list(committee.public_signing_keys)
        if not keys_to_check:
            await quart.flash(f"Committee '{committee_name}' has no keys.", "info")
            return await session.redirect(delete_committee_keys_get)

        num_removed = len(committee.public_signing_keys)
        committee.public_signing_keys.clear()
        await data.flush()

        unused_deleted = 0
        for key_obj in keys_to_check:
            if not key_obj.committees:
                await data.delete(key_obj)
                unused_deleted += 1

        await data.commit()
        await quart.flash(
            f"Removed {util.plural(num_removed, 'key link')} for '{committee_name}'. "
            f"Deleted {util.plural(unused_deleted, 'unused key')}.",
            "success",
        )

    return await session.redirect(delete_committee_keys_get)


@admin.get("/delete-release")
async def delete_release_get(session: web.Committer) -> str | web.WerkzeugResponse:
    """Display the form to delete releases."""
    async with db.session() as data:
        releases = await data.release(_project=True).order_by(sql.Release.name).all()

    if releases:
        releases_widget = htpy.div[
            [
                htpy.div(".form-check")[
                    htpy.input(
                        class_="form-check-input",
                        type="checkbox",
                        name="releases_to_delete",
                        value=release.name,
                        id=f"release_{release.name}",
                    ),
                    htpy.label(".form-check-label", for_=f"release_{release.name}")[
                        htpy.strong[release.name],
                        f" ({release.project.display_name}, {release.phase.value.upper()})",
                    ],
                ]
                for release in releases
            ]
        ]
        block = htm.Block(htm.div)
        block.append(releases_widget)
        block.div(".form-text.mt-1")["Select one or more releases to delete permanently."]
        releases_widget_with_help = block.collect()
    else:
        releases_widget_with_help = htpy.p(".text-muted")["No releases found in the database."]

    rendered_form = form.render(
        model_cls=DeleteReleaseForm,
        submit_label="Delete selected releases permanently",
        submit_classes="btn-danger",
        custom={"releases_to_delete": releases_widget_with_help},
    )

    return await template.render("delete-release.html", form=rendered_form)


@admin.post("/delete-release")
@admin.form(DeleteReleaseForm)
async def delete_release_post(session: web.Committer, delete_form: DeleteReleaseForm) -> str | web.WerkzeugResponse:
    """Delete selected releases and their associated data and files."""
    await _delete_releases(session, delete_form.releases_to_delete)

    return await session.redirect(delete_release_get)


@admin.get("/delete-test-openpgp-keys")
async def delete_test_openpgp_keys_get(session: web.Committer) -> web.Response:
    """Display the form to delete test user OpenPGP keys."""
    if not config.get().ALLOW_TESTS:
        raise base.ASFQuartException("Test operations are disabled in this environment", errorcode=403)

    rendered_form = form.render(
        model_cls=form.Empty,
        submit_label="Delete all OpenPGP keys for test user",
        empty=True,
    )
    return web.ElementResponse(rendered_form)


@admin.post("/delete-test-openpgp-keys")
@admin.empty()
async def delete_test_openpgp_keys_post(session: web.Committer) -> web.Response:
    """Delete all test user OpenPGP keys and their links."""
    if not config.get().ALLOW_TESTS:
        raise base.ASFQuartException("Test operations are disabled in this environment", errorcode=403)

    test_uid = "test"
    try:
        async with storage.write() as write:
            wafc = write.as_foundation_committer()
            delete_outcome = await wafc.keys.test_user_delete_all(test_uid)
            deleted_count = delete_outcome.result_or_raise()

        await quart.flash(
            f"Successfully deleted {util.plural(deleted_count, 'OpenPGP key')} "
            "and their associated links for test user.",
            "success",
        )
    except Exception as e:
        log.exception("Error deleting test user keys:")
        await quart.flash(f"Error deleting test user keys: {e!s}", "error")

    return await session.redirect(get.keys.keys)


@admin.get("/env")
async def env(session: web.Committer) -> web.QuartResponse:
    """Display the environment variables."""
    env_vars = []
    for key, value in os.environ.items():
        env_vars.append(f"{key}={value}")
    return web.TextResponse("\n".join(env_vars))


@admin.get("/keys/check")
async def keys_check_get(session: web.Committer) -> web.QuartResponse:
    """Check public signing key details."""
    rendered_form = form.render(
        model_cls=form.Empty,
        submit_label="Check public signing key details",
        empty=True,
    )
    return web.ElementResponse(rendered_form)


@admin.post("/keys/check")
async def keys_check_post(session: web.Committer) -> web.QuartResponse:
    """Check public signing key details."""
    try:
        result = await _check_keys()
        return web.TextResponse(result)
    except Exception as e:
        log.exception("Exception during key check:")
        return web.TextResponse(f"Exception during key check: {e!s}")


@admin.get("/keys/regenerate-all")
async def keys_regenerate_all_get(session: web.Committer) -> web.QuartResponse:
    """Display the form to regenerate KEYS files."""
    rendered_form = form.render(
        model_cls=form.Empty,
        submit_label="Regenerate all KEYS files",
        empty=True,
    )
    return web.ElementResponse(rendered_form)


@admin.post("/keys/regenerate-all")
async def keys_regenerate_all_post(session: web.Committer) -> web.QuartResponse:
    """Regenerate the KEYS file for all committees."""
    async with db.session() as data:
        committee_names = [c.name for c in await data.committee().all()]

    outcomes = outcome.List[str]()
    async with storage.write() as write:
        for committee_name in committee_names:
            wacm_outcome = write.as_committee_member_outcome(committee_name)
            wacm = wacm_outcome.result_or_none()
            if wacm is None:
                continue
            outcomes.append(await wacm.keys.autogenerate_keys_file())

    response_lines = []
    for ocr in outcomes.results():
        response_lines.append(f"Regenerated: {ocr}")
    for oce in outcomes.errors():
        response_lines.append(f"Error regenerating: {type(oce).__name__} {oce}")

    return web.TextResponse("\n".join(response_lines))


@admin.get("/keys/update")
async def keys_update_get(session: web.Committer) -> str | web.WerkzeugResponse | tuple[Mapping[str, Any], int]:
    """Update keys from remote data."""
    rendered_form = form.render(
        model_cls=form.Empty,
        submit_label="Update keys",
        empty=True,
        form_classes="",
    )
    # TODO: All known file paths should be constants
    log_path = pathlib.Path("logs") / "keys-import.log"
    if not await aiofiles.os.path.exists(log_path):
        previous_output = None
    else:
        async with aiofiles.open(log_path) as f:
            previous_output = await f.read()
    return await template.render("update-keys.html", empty_form=rendered_form, previous_output=previous_output)


@admin.post("/keys/update")
async def keys_update_post(session: web.Committer) -> str | web.WerkzeugResponse | tuple[Mapping[str, Any], int]:
    """Update keys from remote data."""
    try:
        pid = await _update_keys(session.asf_uid)
        return {
            "message": f"Successfully started key update process with PID {pid}",
            "category": "success",
        }, 200
    except Exception as e:
        detail = _format_exception_location(e)
        log.exception(f"Failed to start key update process: {detail}")
        return {
            "message": f"Failed to update keys: {detail}",
            "category": "error",
        }, 200


@admin.get("/ldap")
async def ldap_get(session: web.Committer) -> str:
    rendered_form = form.render(
        model_cls=LdapLookupForm,
        submit_label="Lookup",
    )
    return await template.render(
        "ldap-lookup.html",
        form=rendered_form,
        ldap_params=None,
        asf_id=session.asf_uid,
        ldap_query_performed=False,
        uid_query=None,
    )


@admin.post("/ldap")
@admin.form(LdapLookupForm)
async def ldap_post(session: web.Committer, lookup_form: LdapLookupForm) -> str:
    # TODO: This is one case where we should perhaps allow str | None on the form
    uid_query = lookup_form.uid if lookup_form.uid else None
    email_query = lookup_form.email if lookup_form.email else None

    ldap_params: ldap.SearchParameters | None = None
    if uid_query or email_query:
        bind_dn = quart.current_app.config.get("LDAP_BIND_DN")
        bind_password = quart.current_app.config.get("LDAP_BIND_PASSWORD")

        start = time.perf_counter_ns()
        ldap_params = ldap.SearchParameters(
            uid_query=uid_query,
            email_query=email_query,
            bind_dn_from_config=bind_dn,
            bind_password_from_config=bind_password,
            email_only=False,
        )
        await asyncio.to_thread(ldap.search, ldap_params)
        end = time.perf_counter_ns()
        log.info(f"LDAP search took {(end - start) / 1000000} ms")

    rendered_form = form.render(
        model_cls=LdapLookupForm,
        submit_label="Lookup",
        defaults={"uid": uid_query, "email": email_query},
    )

    return await template.render(
        "ldap-lookup.html",
        form=rendered_form,
        ldap_params=ldap_params,
        asf_id=session.asf_uid,
        ldap_query_performed=ldap_params is not None,
        uid_query=uid_query,
    )


@admin.get("/logs")
async def logs(session: web.Committer) -> web.QuartResponse:
    conf = config.get()
    debug_and_allow_tests = (config.get_mode() == config.Mode.Debug) and conf.ALLOW_TESTS
    if not debug_and_allow_tests:
        raise base.ASFQuartException("Not available without ALLOW_TESTS", errorcode=403)
    recent_logs = log.get_recent_logs()
    if recent_logs is None:
        raise base.ASFQuartException("Debug logging not initialised", errorcode=404)
    return web.TextResponse("\n".join(recent_logs))


@admin.get("/ongoing-tasks/<project_name>/<version_name>/<revision>")
async def ongoing_tasks_get(
    session: web.Committer, project_name: str, version_name: str, revision: str
) -> web.QuartResponse:
    return await _ongoing_tasks(session, project_name, version_name, revision)


@admin.post("/ongoing-tasks/<project_name>/<version_name>/<revision>")
async def ongoing_tasks_post(
    session: web.Committer, project_name: str, version_name: str, revision: str
) -> web.QuartResponse:
    return await _ongoing_tasks(session, project_name, version_name, revision)


@admin.get("/performance")
async def performance(session: web.Committer) -> str:
    """Display performance statistics for all routes."""
    app = asfquart.APP

    if app is ...:
        raise base.ASFQuartException("APP is not set", errorcode=500)

    # Read and parse the performance log file
    log_path = pathlib.Path("logs") / "route-performance.log"
    # # Show current working directory and its files
    # cwd = await asyncio.to_thread(Path.cwd)
    # await asyncio.to_thread(APP.logger.info, "Current working directory: %s", cwd)
    # iterable = await asyncio.to_thread(cwd.iterdir)
    # files = list(iterable)
    # await asyncio.to_thread(APP.logger.info, "Files in current directory: %s", files)
    if not await aiofiles.os.path.exists(log_path):
        await quart.flash("No performance data currently available", "error")
        return await template.render("performance.html", stats=None)

    # Parse the log file and collect statistics
    stats = collections.defaultdict(list)
    async with aiofiles.open(log_path) as f:
        async for line in f:
            try:
                _, _, _, methods, path, func, _, sync_ms, async_ms, total_ms = line.strip().split(" ")
                stats[path].append(
                    {
                        "methods": methods,
                        "function": func,
                        "sync_ms": int(sync_ms),
                        "async_ms": int(async_ms),
                        "total_ms": int(total_ms),
                        "timestamp": line.split(" - ")[0],
                    }
                )
            except (ValueError, IndexError):
                log.error(f"Error parsing line: {line}")
                continue

    # Calculate summary statistics for each route
    summary = {}
    for path, timings in stats.items():
        total_times = [int(str(t["total_ms"])) for t in timings]
        sync_times = [int(str(t["sync_ms"])) for t in timings]
        async_times = [int(str(t["async_ms"])) for t in timings]

        summary[path] = {
            "count": len(timings),
            "methods": timings[0]["methods"],
            "function": timings[0]["function"],
            "total": {
                "mean": statistics.mean(total_times),
                "median": statistics.median(total_times),
                "min": min(total_times),
                "max": max(total_times),
                "stdev": statistics.stdev(total_times) if (len(total_times) > 1) else 0,
            },
            "sync": {
                "mean": statistics.mean(sync_times),
                "median": statistics.median(sync_times),
                "min": min(sync_times),
                "max": max(sync_times),
            },
            "async": {
                "mean": statistics.mean(async_times),
                "median": statistics.median(async_times),
                "min": min(async_times),
                "max": max(async_times),
            },
            "last_timestamp": timings[-1]["timestamp"],
        }

    # Sort routes by average total time, descending
    def one_total_mean(x: tuple[str, dict]) -> float:
        return x[1]["total"]["mean"]

    sorted_summary = dict(sorted(summary.items(), key=one_total_mean, reverse=True))
    return await template.render("performance.html", stats=sorted_summary)


@admin.get("/projects/update")
async def projects_update_get(session: web.Committer) -> str | web.WerkzeugResponse | tuple[Mapping[str, Any], int]:
    """Update projects from remote data."""
    rendered_form = form.render(
        model_cls=form.Empty,
        submit_label="Update projects",
        empty=True,
        form_classes="",
    )
    return await template.render("update-projects.html", empty_form=rendered_form)


@admin.post("/projects/update")
async def projects_update_post(session: web.Committer) -> str | web.WerkzeugResponse | tuple[Mapping[str, Any], int]:
    """Update projects from remote data."""
    try:
        task = await tasks.metadata_update(session.asf_uid)
        return {
            "message": f"Metadata update task has been queued with ID {task.id}.",
            "category": "success",
        }, 200
    except Exception as e:
        log.exception("Failed to queue metadata update task")
        return {
            "message": f"Failed to queue metadata update: {e!s}",
            "category": "error",
        }, 200


@admin.get("/task-times/<project_name>/<version_name>/<revision_number>")
async def task_times(
    session: web.Committer, project_name: str, version_name: str, revision_number: str
) -> web.QuartResponse:
    values = []
    async with db.session() as data:
        tasks = await data.task(
            project_name=project_name, version_name=version_name, revision_number=revision_number
        ).all()
        for task in tasks:
            if (task.started is None) or (task.completed is None):
                continue
            ms_elapsed = (task.completed - task.started).total_seconds() * 1000
            values.append(f"{task.task_type} {ms_elapsed:.2f}ms")

    return web.TextResponse("\n".join(values))


@admin.get("/tasks/recent/<int:minutes>")
async def tasks_recent(session: web.Committer, minutes: int) -> str:
    """Display tasks from the last N minutes."""
    if minutes < 1:
        minutes = 1
    if minutes > 1440:
        minutes = 1440

    via = sql.validate_instrumented_attribute
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes)

    async with db.session() as data:
        statement = sqlmodel.select(sql.Task).where(via(sql.Task.added) >= cutoff).order_by(via(sql.Task.added).desc())
        recent_tasks = (await data.execute(statement)).scalars().all()

    page = htm.Block()
    page.h1[f"Tasks from the last {util.plural(minutes, 'minute')}"]
    page.p[f"Found {util.plural(len(recent_tasks), 'task')}"]

    if recent_tasks:
        table = htm.Block(htpy.table, classes=".table.table-sm")
        table.thead(".table-dark")[
            htpy.tr[
                htpy.th["ID"],
                htpy.th["Type"],
                htpy.th["Status"],
                htpy.th["Added"],
                htpy.th["Took"],
                htpy.th["Project"],
                htpy.th["Version"],
                htpy.th["Revision"],
                htpy.th["Error"],
            ]
        ]
        tbody = htm.Block(htpy.tbody)
        for task in recent_tasks:
            error_text = task.error[:50] + "..." if (task.error and (len(task.error) > 50)) else (task.error or "")
            status_class = {
                sql.TaskStatus.QUEUED: ".table-secondary",
                sql.TaskStatus.ACTIVE: ".table-info",
                sql.TaskStatus.COMPLETED: ".table-success",
                sql.TaskStatus.FAILED: ".table-danger",
            }.get(task.status, "")
            if (task.started is not None) and (task.completed is not None):
                took_seconds = (task.completed - task.started).total_seconds()
                took_text = f"{took_seconds:.1f}s"
            else:
                took_text = ""
            tbody.append(
                htpy.tr(class_=status_class.lstrip(".") if status_class else None)[
                    htpy.td[str(task.id)],
                    htpy.td[task.task_type.value],
                    htpy.td[task.status.value],
                    htpy.td[task.added.strftime("%H:%M:%S") if task.added else ""],
                    htpy.td[took_text],
                    htpy.td[task.project_name or ""],
                    htpy.td[task.version_name or ""],
                    htpy.td[task.revision_number or ""],
                    htpy.td[error_text],
                ]
            )
        table.append(tbody.collect())
        page.append(table.collect())

    return await template.blank(f"Recent Tasks ({minutes}m)", content=page.collect())


@admin.get("/test")
async def test(session: web.Committer) -> web.QuartResponse:
    """Test the storage layer."""
    import atr.storage as storage

    async with aiohttp.ClientSession() as aiohttp_client_session:
        url = "https://downloads.apache.org/zeppelin/KEYS"
        async with aiohttp_client_session.get(url) as response:
            keys_file_text = await response.text()

    async with storage.write(session) as write:
        wacm = write.as_committee_member("tooling")
        start = time.perf_counter_ns()
        outcomes: outcome.List[types.Key] = await wacm.keys.ensure_stored(keys_file_text)
        end = time.perf_counter_ns()
        log.info(f"Upload of {util.plural(outcomes.result_count, 'key')} took {end - start} ns")
    for ocr in outcomes.results():
        log.info(f"Uploaded key: {type(ocr)} {ocr.key_model.fingerprint}")
    for oce in outcomes.errors():
        log.error(f"Error uploading key: {type(oce)} {oce}")
    parsed_count = outcomes.result_predicate_count(lambda k: k.status == types.KeyStatus.PARSED)
    inserted_count = outcomes.result_predicate_count(lambda k: k.status == types.KeyStatus.INSERTED)
    linked_count = outcomes.result_predicate_count(lambda k: k.status == types.KeyStatus.LINKED)
    inserted_and_linked_count = outcomes.result_predicate_count(
        lambda k: k.status == types.KeyStatus.INSERTED_AND_LINKED
    )
    log.info(f"Parsed: {parsed_count}")
    log.info(f"Inserted: {inserted_count}")
    log.info(f"Linked: {linked_count}")
    log.info(f"InsertedAndLinked: {inserted_and_linked_count}")
    return web.TextResponse(str(wacm))


@admin.get("/toggle-view")
async def toggle_view_get(session: web.Committer) -> str:
    """Display the page with a button to toggle between admin and user views."""
    rendered_form = form.render(
        model_cls=form.Empty,
        submit_label="Toggle view",
        empty=True,
        form_classes=".mb-4",
    )
    return await template.render("toggle-admin-view.html", empty_form=rendered_form)


@admin.post("/toggle-view")
@admin.empty()
async def toggle_view_post(session: web.Committer) -> web.WerkzeugResponse:
    """Toggle between admin and user views."""
    app = asfquart.APP
    if (not hasattr(app, "app_id")) or (not isinstance(app.app_id, str)):
        raise TypeError("Internal error: APP has no valid app_id")

    cookie_id = app.app_id
    session_dict = quart.session.get(cookie_id, {})
    downgrade = not session_dict.get("downgrade_admin_to_user", False)
    session_dict["downgrade_admin_to_user"] = downgrade

    message = "Viewing as regular user" if downgrade else "Viewing as admin"
    await quart.flash(message, "success")
    referrer = quart.request.referrer
    return quart.redirect(referrer or util.as_url(data))


@admin.get("/validate")
async def validate_(session: web.Committer) -> str:
    """Run validators and display any divergences."""

    async with db.session() as data:
        divergences = [d async for d in validate.everything(data)]

    return await template.render(
        "validation.html",
        divergences=divergences,
    )


async def _check_keys(fix: bool = False) -> str:
    email_to_uid = await util.email_to_uid_map()
    bad_keys = []
    async with db.session() as data:
        keys = await data.public_signing_key().all()
        for key in keys:
            uids = []
            if key.primary_declared_uid:
                uids.append(key.primary_declared_uid)
            if key.secondary_declared_uids:
                uids.extend(key.secondary_declared_uids)
            asf_uid = await util.asf_uid_from_uids(uids, ldap_data=email_to_uid)
            if asf_uid != key.apache_uid:
                bad_keys.append(f"{key.fingerprint} detected: {asf_uid}, key: {key.apache_uid}")
            if fix:
                key.apache_uid = asf_uid
                await data.commit()
    message = f"Checked {util.plural(len(keys), 'key')}"
    if bad_keys:
        message += f"\nFound {util.plural(len(bad_keys), 'bad key')}:\n{'\n'.join(bad_keys)}"
    return message


async def _data(session: web.Committer, model: str = "Committee") -> str:
    """Browse all records in the database."""
    async with db.session() as data:
        # Map of model names to their classes
        # TODO: Add distribution channel, key link, and any others
        model_methods: dict[str, Callable[[], db.Query[Any]]] = {
            "CheckResult": data.check_result,
            "CheckResultIgnore": data.check_result_ignore,
            "Committee": data.committee,
            "Project": data.project,
            "PublicSigningKey": data.public_signing_key,
            "Release": data.release,
            "ReleasePolicy": data.release_policy,
            "Revision": data.revision,
            "SSHKey": data.ssh_key,
            "Task": data.task,
            "TextValue": data.text_value,
        }

        if model not in model_methods:
            raise base.ASFQuartException(f"Model type '{model}' not found", 404)

        # Get all records for the selected model
        records = await model_methods[model]().all()

        # Convert records to dictionaries for JSON serialization
        records_dict = []
        for record in records:
            if hasattr(record, "dict"):
                record_dict = record.dict()
            else:
                # Fallback for models without dict() method
                record_dict = {}
                # record_dict = {
                #     "id": getattr(record, "id", None),
                #     "name": getattr(record, "name", None),
                # }
                for key in record.__dict__:
                    if not key.startswith("_"):
                        record_dict[key] = getattr(record, key)
            records_dict.append(record_dict)

        return await template.render(
            "data-browser.html", models=list(model_methods.keys()), model=model, records=records_dict
        )


async def _delete_releases(session: web.Committer, releases_to_delete: list[str]) -> None:
    success_count = 0
    fail_count = 0
    error_messages = []

    for release_name in releases_to_delete:
        try:
            async with db.session() as data:
                release = await data.release(name=release_name, _committee=True, _project=True).demand(
                    RuntimeError(f"Release {release_name} not found")
                )
                if release.committee is None:
                    raise RuntimeError(f"Release {release_name} has no committee")
            async with storage.write(session) as write:
                wafa = write.as_foundation_admin(release.committee.name)
                await wafa.release.delete(release.project.name, release.version)
            success_count += 1
        except base.ASFQuartException as e:
            log.error(f"Error deleting release {release_name}: {e}")
            fail_count += 1
            error_messages.append(f"{release_name}: {e}")
        except Exception as e:
            log.exception(f"Unexpected error deleting release {release_name}:")
            fail_count += 1
            error_messages.append(f"{release_name}: Unexpected error ({e})")

    if success_count > 0:
        await quart.flash(f"Successfully deleted {util.plural(success_count, 'release')}.", "success")
    if fail_count > 0:
        errors_str = "\n".join(error_messages)
        await quart.flash(f"Failed to delete {util.plural(fail_count, 'release')}:\n{errors_str}", "error")


def _format_exception_location(exc: BaseException) -> str:
    tb = exc.__traceback__
    last_tb = None
    while tb is not None:
        last_tb = tb
        tb = tb.tb_next
    if last_tb is None:
        return f"{type(exc).__name__}: {exc}"
    frame = last_tb.tb_frame
    filename = pathlib.Path(frame.f_code.co_filename).name
    lineno = last_tb.tb_lineno
    func = frame.f_code.co_name
    return f"{type(exc).__name__} at {filename}:{lineno} in {func}: {exc}"


async def _get_filesystem_dirs() -> list[str]:
    filesystem_dirs = []
    await _get_filesystem_dirs_finished(filesystem_dirs)
    await _get_filesystem_dirs_unfinished(filesystem_dirs)
    return filesystem_dirs


async def _get_filesystem_dirs_finished(filesystem_dirs: list[str]) -> None:
    finished_dir = util.get_finished_dir()
    finished_dir_contents = await aiofiles.os.listdir(finished_dir)
    for project_dir in finished_dir_contents:
        project_dir_path = os.path.join(finished_dir, project_dir)
        if await aiofiles.os.path.isdir(project_dir_path):
            for version_dir in await aiofiles.os.listdir(project_dir_path):
                if await aiofiles.os.path.isdir(os.path.join(project_dir_path, version_dir)):
                    version_dir_path = os.path.join(project_dir_path, version_dir)
                    if await aiofiles.os.path.isdir(version_dir_path):
                        filesystem_dirs.append(version_dir_path)


async def _get_filesystem_dirs_unfinished(filesystem_dirs: list[str]) -> None:
    unfinished_dir = util.get_unfinished_dir()
    unfinished_dir_contents = await aiofiles.os.listdir(unfinished_dir)
    for project_dir in unfinished_dir_contents:
        project_dir_path = os.path.join(unfinished_dir, project_dir)
        if await aiofiles.os.path.isdir(project_dir_path):
            for version_dir in await aiofiles.os.listdir(project_dir_path):
                if await aiofiles.os.path.isdir(os.path.join(project_dir_path, version_dir)):
                    version_dir_path = os.path.join(project_dir_path, version_dir)
                    if await aiofiles.os.path.isdir(version_dir_path):
                        filesystem_dirs.append(version_dir_path)


def _get_user_committees_from_ldap(uid: str, bind_dn: str, bind_password: str) -> set[str]:
    escaped_uid = conv.escape_filter_chars(uid)
    with ldap.Search(bind_dn, bind_password) as ldap_search:
        result = ldap_search.search(
            ldap_base="ou=project,ou=groups,dc=apache,dc=org",
            ldap_scope="SUBTREE",
            ldap_query=f"(|(ownerUid={escaped_uid})(owner=uid={escaped_uid},ou=people,dc=apache,dc=org))",
            ldap_attrs=["cn"],
        )

    committees = set()
    for hit in result:
        if not isinstance(hit, dict):
            continue
        pmc = hit.get("cn")
        if not (isinstance(pmc, list) and (len(pmc) == 1)):
            continue
        project_name = pmc[0]
        if project_name and isinstance(project_name, str):
            committees.add(project_name)

    return committees


def _log_safe_session_data(
    ldap_data: dict[str, Any],
    new_uid: str,
    ldap_projects: apache.LDAPProjectsData,
    committee_data: apache.CommitteeData,
    bind_dn: str,
    bind_password: str,
) -> dict[str, Any]:
    # This version excludes "mfa" which CodeQL treats as sensitive
    # It's just a bool, but we can't suppress the error at source
    common = _session_data_common(ldap_data, new_uid, ldap_projects, committee_data, bind_dn, bind_password)
    return {
        "uid": common.uid,
        "dn": None,
        "fullname": common.fullname,
        "email": common.email,
        "isMember": common.is_member,
        "isChair": common.is_chair,
        "isRoot": common.is_root,
        "pmcs": common.pmcs,
        "projects": common.projects,
        "isRole": False,
        "metadata": {},
    }


async def _ongoing_tasks(
    session: web.Committer, project_name: str, version_name: str, revision: str
) -> web.QuartResponse:
    try:
        ongoing = await interaction.tasks_ongoing(project_name, version_name, revision)
        return web.TextResponse(str(ongoing))
    except Exception:
        log.exception(f"Error fetching ongoing task count for {project_name} {version_name} rev {revision}:")
        return web.TextResponse("")


def _session_data(
    ldap_data: dict[str, Any],
    new_uid: str,
    current_session: asfquart.session.ClientSession,
    ldap_projects: apache.LDAPProjectsData,
    committee_data: apache.CommitteeData,
    bind_dn: str,
    bind_password: str,
) -> dict[str, Any]:
    common = _session_data_common(ldap_data, new_uid, ldap_projects, committee_data, bind_dn, bind_password)
    return {
        "uid": common.uid,
        "dn": None,
        "fullname": common.fullname,
        "email": common.email,
        "isMember": common.is_member,
        "isChair": common.is_chair,
        "isRoot": common.is_root,
        # WARNING: ASFQuart session.ClientSession uses "committees"
        # But this is cookie, not ClientSession, data, and requires "pmcs"
        "pmcs": common.pmcs,
        "projects": common.projects,
        "mfa": current_session.mfa,
        "isRole": False,
        "metadata": {},
    }


def _session_data_common(
    ldap_data: dict[str, Any],
    new_uid: str,
    ldap_projects: apache.LDAPProjectsData,
    committee_data: apache.CommitteeData,
    bind_dn: str,
    bind_password: str,
) -> SessionDataCommon:
    # This is not quite accurate
    # For example, this misses "tooling" for tooling members
    projects = {p.name for p in ldap_projects.projects if (new_uid in p.members) or (new_uid in p.owners)}
    # And this adds "incubator", which is not in the OAuth data
    committees = _get_user_committees_from_ldap(new_uid, bind_dn, bind_password)

    # Or asf-member-status?
    is_member = bool(projects or committees)
    is_root = False
    is_chair = any(new_uid in (user.id for user in c.chair) for c in committee_data.committees)

    return SessionDataCommon(
        uid=ldap_data.get("uid", [new_uid])[0],
        fullname=ldap_data.get("cn", [new_uid])[0],
        email=f"{new_uid}@apache.org",
        is_member=is_member,
        is_chair=is_chair,
        is_root=is_root,
        pmcs=sorted(list(committees)),
        projects=sorted(list(projects)),
    )


async def _update_keys(asf_uid: str) -> int:
    async def _log_process(process: asyncio.subprocess.Process) -> None:
        try:
            stdout, stderr = await process.communicate()
            if stdout:
                log.info(f"keys_import.py stdout:\n{stdout.decode('utf-8')[:1000]}")
            if stderr:
                log.error(f"keys_import.py stderr:\n{stderr.decode('utf-8')[:1000]}")
        except Exception:
            log.exception("Error reading from subprocess for keys_import.py")

    app = asfquart.APP
    if not hasattr(app, "background_tasks"):
        app.background_tasks = set()

    if await aiofiles.os.path.exists("../Dockerfile.alpine"):
        # Not in a container, developing locally
        command = ["poetry", "run", "python3", "scripts/keys_import.py", asf_uid]
    else:
        # In a container
        command = [sys.executable, "scripts/keys_import.py", asf_uid]

    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=".."
    )

    task = asyncio.create_task(_log_process(process))
    app.background_tasks.add(task)
    task.add_done_callback(app.background_tasks.discard)

    return process.pid
