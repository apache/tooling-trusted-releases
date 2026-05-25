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
import hashlib
import ipaddress
import json
import os
import pathlib
import statistics
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any, Final, Literal, NamedTuple

import aiofiles.os
import asfquart
import asfquart.base as base
import htpy
import jwt
import pydantic
import quart
import sqlalchemy
import sqlmodel
import werkzeug.exceptions as exceptions

import atr.blueprints.admin as admin
import atr.cache as cache
import atr.config as config
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get as get
import atr.htm as htm
import atr.jwtoken as jwtoken
import atr.ldap as ldap
import atr.log as log
import atr.mapping as mapping
import atr.models.api as api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.models.validation as validation
import atr.noisy as noisy
import atr.paths as paths
import atr.principal as principal
import atr.shared as shared
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.tasks as tasks
import atr.template as template
import atr.util as util
import atr.validate as validate
import atr.web as web

ROUTES_MODULE: Final[Literal[True]] = True

_MAXIMUM_PERMITTED_AGE_DAYS: Final = datetime.timedelta(days=90)


class BrowseAsUserForm(form.Form):
    uid: str = form.label("ASF UID", "Enter the ASF UID to browse as.")


class DeleteCommitteeKeysForm(form.Form):
    committee_key: str = form.label("Committee", widget=form.Widget.SELECT)
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
    email: form.OptionalEmail = form.label(
        "Email address (optional)", "Enter email address, e.g. user@example.org", widget=form.Widget.EMAIL
    )


class ReleaseAgeRow(NamedTuple):
    release: sql.Release
    age_label: str
    age_bold: bool
    inactive_label: str
    inactive_bold: bool


class RevokeUserTokensForm(form.Form):
    asf_uid: str = form.label("ASF UID", "Enter the ASF UID whose tokens should be revoked.")
    confirm_revoke: Literal["REVOKE"] = form.label("Confirmation", "Type REVOKE to confirm.")


class RevokeUserSSHKeysForm(form.Form):
    asf_uid: str = form.label("ASF UID", "Enter the ASF UID whose SSH keys should be revoked.")
    confirm_revoke: Literal["REVOKE"] = form.label("Confirmation", "Type REVOKE to confirm.")


class RotateJwtKeyForm(form.Form):
    confirm_rotate: Literal["ROTATE"] = form.label("Confirmation", "Type ROTATE to confirm.")


class ValidateJwtForm(form.Form):
    token: str = form.label("JWT", "Paste the JWT to validate.", widget=form.Widget.TEXTAREA)

    @pydantic.field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        token = value.strip()
        if token == "":
            raise ValueError("JWT is required")
        return token


class CreateSystemTokenForm(form.Form):
    label: str = form.label("Label", "A name to identify this system token.")
    allowed_ip: str = form.label(
        "Allowed IP",
        "Optional single IP or CIDR the token may be exchanged from. Leave blank for no restriction.",
        default="",
    )

    @pydantic.field_validator("label", mode="after")
    @classmethod
    def validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Label is required")
        if len(value) > 100:
            raise ValueError("Label must be 100 characters or less")
        return value

    @pydantic.field_validator("allowed_ip", mode="after")
    @classmethod
    def validate_allowed_ip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as e:
            raise ValueError("Allowed IP must be a valid IP address or CIDR") from e
        return value


class RevokeSystemTokenForm(form.Form):
    token_id: form.Int = form.label("Token ID", widget=form.Widget.HIDDEN)


class SessionDataCommon(NamedTuple):
    uid: str
    fullname: str
    email: str
    is_member: bool
    is_chair: bool
    is_root: bool
    pmcs: list[str]
    projects: list[str]


@admin.typed
async def all_releases(_session: web.Committer, _all_releases: Literal["all/releases"]) -> str:
    """
    URL: GET /all/releases

    Display a list of all releases across all phases.
    """
    async with db.session() as data:
        releases = await data.release(_project=True, _committee=True, _revisions=True).order_by(sql.Release.key).all()
    now = datetime.datetime.now(datetime.UTC)
    release_rows = [_release_age_row(release, now) for release in releases]
    return await template.render("all-releases.html", release_rows=release_rows, release_as_url=mapping.release_as_url)


@admin.typed
async def browse_as_get(_session: web.Committer, _browse_as: Literal["browse-as"]) -> str | web.WerkzeugResponse:
    """
    URL: GET /browse-as

    Allows an admin to browse as another user.
    """
    rendered_form = await form.render(
        model_cls=BrowseAsUserForm,
        submit_label="Browse as this user",
    )
    return await template.render("browse-as.html", form=rendered_form)


@admin.typed
async def browse_as_post(
    session: web.Committer, _browse_as: Literal["browse-as"], browse_form: BrowseAsUserForm
) -> str | web.WerkzeugResponse:
    """
    URL: POST /browse-as

    Allows an admin to browse as another user.
    """
    # We specifically need to use this on the production server
    import atr.get.root as root

    new_uid = browse_form.uid

    try:
        committer = principal.Committer(safe.AsfUid(new_uid))
        await asyncio.to_thread(committer.verify)
    except (principal.CommitterError, ValueError) as exc:
        await quart.flash(f"Unable to browse as '{new_uid}': {exc}", "error")
        return await session.redirect(browse_as_get)

    admin_id: str = session.asf_uid
    existing_admin = session.session.admin_uid
    if isinstance(existing_admin, str) and existing_admin:
        admin_id = existing_admin

    await util.write_session(
        sql.UserSession(
            uid=committer.uid,
            fullname=committer.fullname or committer.uid,
            email=committer.email,
            is_member=committer.isMember,
            is_chair=committer.isChair,
            # is_root=committer.isRoot,
            committees=sorted(set(committer.pmcs)),
            projects=sorted(set(committer.projects)),
            mfa=session.session.mfa,
            admin_uid=admin_id,
            ip_address=quart.request.remote_addr,
        )
    )
    log.auth_event("impersonate", admin_id, as_user=committer.uid)

    await quart.flash(
        f"You are now browsing as '{new_uid}'. To return to your own account, please log out and log back in.",
        "success",
    )
    return await session.redirect(root.index)


@admin.typed
async def configuration(_session: web.Committer, _configuration: Literal["configuration"]) -> web.QuartResponse:
    """
    URL: GET /configuration

    Display the current application configuration values.
    """

    sensitive_config_patterns = ("_PASSWORD", "_KEY", "_TOKEN", "_SECRET")

    conf = config.get()
    values: list[str] = []
    for name in dir(conf):
        if name.startswith("_"):
            continue
        try:
            val = getattr(conf, name)
        except Exception as exc:
            val = log.python_repr(f"error: {exc}")
        if any(pattern in name for pattern in sensitive_config_patterns):
            val = log.python_repr("redacted")
        if callable(val):
            continue
        values.append(f"{name}={val}")

    values.sort()
    return web.TextResponse("\n".join(values))


@admin.typed
async def consistency(_session: web.Committer, _consistency: Literal["consistency"]) -> web.TextResponse:
    """
    URL: GET /consistency

    Check for consistency between the database and the filesystem.
    """
    # Get all releases from the database
    async with db.session() as data:
        releases = await data.release().all()
    database_dirs = []
    for release in releases:
        path = paths.release_directory_version(release)
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


@admin.typed
async def data(session: web.Committer, _data: Literal["data"]) -> str:
    """
    URL: GET /data
    """
    return await _data_browse(session, "Committee")


@admin.typed
async def data_model(
    session: web.Committer, _data: Literal["data"], model: unsafe.UnsafeStr = unsafe.UnsafeStr("Committee")
) -> str:
    """
    URL: GET /data/<model>
    """

    return await _data_browse(session, str(model))


@admin.typed
async def delete_committee_keys_get(
    _session: web.Committer, _delete_committee_keys: Literal["delete-committee-keys"]
) -> str | web.WerkzeugResponse:
    """
    URL: GET /delete-committee-keys

    Display the form to delete committee keys.
    """
    async with db.session() as data:
        all_committees = await data.committee(_public_signing_keys=True).order_by(sql.Committee.key).all()
        committees_with_keys = [c for c in all_committees if c.public_signing_keys]

    committee_choices = [(c.key, c.display_name) for c in committees_with_keys]

    rendered_form = await form.render(
        model_cls=DeleteCommitteeKeysForm,
        submit_label="Delete all keys for selected committee",
        defaults={"committee_key": committee_choices},
    )
    return await template.render(
        "admin-form.html",
        title="Delete committee keys",
        description="Delete committee keys",
        header="Delete all keys for a committee",
        form=rendered_form,
    )


@admin.typed
async def delete_committee_keys_post(
    session: web.Committer,
    _delete_committee_keys: Literal["delete-committee-keys"],
    delete_form: DeleteCommitteeKeysForm,
) -> str | web.WerkzeugResponse:
    """
    URL: POST /delete-committee-keys

    Delete all keys for selected committee.
    """
    committee_key = delete_form.committee_key

    try:
        async with storage.write(session) as write:
            waca = write.as_committee_admin(committee_key)
            num_unlinked, num_deleted = await waca.keys.delete_committee_keys()
    except storage.AccessError as e:
        await quart.flash(str(e), "error")
        return await session.redirect(delete_committee_keys_get)

    if num_unlinked == 0:
        await quart.flash(f"Committee '{committee_key}' has no keys.", "info")
    else:
        await quart.flash(
            f"Removed {util.plural(num_unlinked, 'key link')} for '{committee_key}'. "
            f"Deleted {util.plural(num_deleted, 'unused key')}.",
            "success",
        )

    return await session.redirect(delete_committee_keys_get)


@admin.typed
async def delete_release_get(
    _session: web.Committer, _delete_release_get: Literal["delete-release"]
) -> str | web.WerkzeugResponse:
    """
    URL: GET /delete-release

    Display the form to delete releases.
    """
    async with db.session() as data:
        releases = await data.release(_project=True).order_by(sql.Release.key).all()

    if releases:
        releases_widget = htpy.div[
            [
                htpy.div(".form-check")[
                    htpy.input(
                        class_="form-check-input",
                        type="checkbox",
                        name="releases_to_delete",
                        value=release.key,
                        id=f"release_{release.key}",
                    ),
                    htpy.label(".form-check-label", for_=f"release_{release.key}")[
                        htpy.strong[release.key],
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

    rendered_form = await form.render(
        model_cls=DeleteReleaseForm,
        submit_label="Delete selected releases permanently",
        submit_classes="btn-danger",
        custom={"releases_to_delete": releases_widget_with_help},
    )

    return await template.render("delete-release.html", form=rendered_form)


@admin.typed
async def delete_release_post(
    session: web.Committer, _delete_release_get: Literal["delete-release"], delete_form: DeleteReleaseForm
) -> str | web.WerkzeugResponse:
    """
    URL: POST /delete-release

    Delete selected releases and their associated data and files.
    """
    await _delete_releases(session, delete_form.releases_to_delete)

    return await session.redirect(delete_release_get)


@admin.typed
async def delete_test_openpgp_keys_get(
    _session: web.Committer, _delete_test_openpgp_keys: Literal["delete-test-openpgp-keys"]
) -> web.Response:
    """
    URL: GET /delete-test-openpgp-keys

    Display the form to delete test user OpenPGP keys.
    This is a test-only endpoint so doesn't need any of the usual UI.
    """
    if not config.is_test_mode():
        return quart.abort(404)

    rendered_form = await form.render(
        model_cls=form.Empty,
        submit_label="Delete all OpenPGP keys for test user",
        empty=True,
    )
    return web.ElementResponse(rendered_form)


@admin.typed
async def delete_test_openpgp_keys_post(
    session: web.Committer, _delete_test_openpgp_keys: Literal["delete-test-openpgp-keys"], _form: form.Empty
) -> web.Response:
    """
    URL: POST /delete-test-openpgp-keys

    Delete all test user OpenPGP keys and their links.
    """
    if not config.is_test_mode():
        return quart.abort(404)

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


@admin.typed
async def keys_check_get(_session: web.Committer, _keys_check: Literal["keys/check"]) -> str | web.WerkzeugResponse:
    """
    URL: GET /keys/check

    Check public signing key details.
    """
    rendered_form = await form.render(
        model_cls=form.Empty,
        submit_label="Check",
        empty=True,
    )
    return await template.render(
        "admin-form.html",
        title="Check public signing key details",
        description="Check public signing key details",
        header="Check public signing key details",
        form=rendered_form,
    )


@admin.typed
async def keys_check_post(
    _session: web.Committer, _keys_check: Literal["keys/check"], _form: form.Empty
) -> str | web.WerkzeugResponse:
    """Check public signing key details."""
    page = htm.Block()
    page.h1["Public signing key check results"]
    try:
        result = await _check_keys()
        page.div[[htm.p[line] for line in result.split("\n")]]
    except Exception as e:
        log.exception("Exception during key check:")
        page.p[f"Exception during key check: {e!s}"]
    return await template.render(
        "blank.html",
        title="Check public signing key details",
        description="Check public signing key details",
        content=page.collect(),
    )


@admin.typed
async def keys_regenerate_all_get(
    _session: web.Committer, _keys_regenerate_all: Literal["keys/regenerate-all"]
) -> str | web.WerkzeugResponse:
    """
    URL: GET /keys/regenerate-all

    Display the form to regenerate KEYS files.
    """
    rendered_form = await form.render(
        model_cls=form.Empty,
        submit_label="Re-generate",
        empty=True,
    )
    return await template.render(
        "admin-form.html",
        title="Regenerate KEYS files",
        description="Regenerate KEYS files",
        header="Regenerate all KEYS files",
        form=rendered_form,
    )


@admin.typed
async def keys_regenerate_all_post(
    _session: web.Committer, _keys_regenerate_all: Literal["keys/regenerate-all"], _form: form.Empty
) -> web.QuartResponse:
    """Regenerate the KEYS file for all committees."""
    async with db.session() as data:
        committee_keys = [c.key for c in await data.committee().all()]

    outcomes = outcome.List[str]()
    async with storage.write() as write:
        for committee_key in committee_keys:
            waca = write.as_committee_admin(committee_key)
            keys_file_outcome, _ = await waca.keys.autogenerate_keys_file()
            outcomes.append(keys_file_outcome)

    response_lines = []
    for ocr in outcomes.results():
        response_lines.append(f"Regenerated: {ocr}")
    for oce in outcomes.errors():
        response_lines.append(f"Error regenerating: {type(oce).__name__} {oce}")

    return web.TextResponse("\n".join(response_lines))


@admin.typed
async def keys_update_get(
    _session: web.Committer, _keys_update: Literal["keys/update"]
) -> str | web.WerkzeugResponse | tuple[Mapping[str, Any], int]:
    """
    URL: GET /keys/update

    Update keys from remote data.
    """
    rendered_form = await form.render(
        model_cls=form.Empty,
        submit_label="Update keys",
        empty=True,
        form_classes="",
    )
    # TODO: All known file paths should be constants
    log_path = pathlib.Path(config.get().STATE_DIR) / "logs" / "keys-import.log"
    if not await aiofiles.os.path.exists(log_path):
        previous_output = None
    else:
        async with aiofiles.open(log_path) as f:
            previous_output = await f.read()
    return await template.render("update-keys.html", empty_form=rendered_form, previous_output=previous_output)


@admin.typed
async def keys_update_post(
    session: web.Committer, _keys_update: Literal["keys/update"], _form: form.Empty
) -> str | web.WerkzeugResponse | tuple[Mapping[str, Any], int]:
    """Update keys from remote data."""
    try:
        pid = await _update_keys(session.asf_uid)
        log.info(f"Keys update process started with PID {pid}")
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


@admin.typed
async def ldap_get(session: web.Committer, _ldap: Literal["ldap"]) -> str:
    """
    URL: GET /ldap
    """
    rendered_form = await form.render(
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


@admin.typed
async def ldap_post(session: web.Committer, _ldap: Literal["ldap"], lookup_form: LdapLookupForm) -> str:
    """
    URL POST /ldap
    """
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
        )
        await asyncio.to_thread(ldap.search, ldap_params)
        end = time.perf_counter_ns()
        log.info(f"LDAP search took {(end - start) / 1000000} ms")

    rendered_form = await form.render(
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


@admin.typed
async def logs(_session: web.Committer, _logs: Literal["logs"]) -> web.QuartResponse:
    """
    URL: GET /logs
    """
    _require_non_production_mode()
    recent_logs = log.get_recent_logs()
    if recent_logs is None:
        raise base.ASFQuartException("Debug logging not initialised", errorcode=404)
    return web.TextResponse("\n".join(recent_logs))


@admin.typed
async def ongoing_tasks_get(
    session: web.Committer,
    _ongoing_tasks: Literal["ongoing-tasks"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber,
) -> web.QuartResponse:
    """
    URL: GET /ongoing-tasks
    """
    return await _fetch_ongoing_tasks(session, project_key, version_key, revision)


@admin.typed
async def performance(_session: web.Committer, _performance: Literal["performance"]) -> str:
    """
    URL: GET /performance

    Display performance statistics for all routes.
    """
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


@admin.typed
async def projects_update_get(
    _session: web.Committer, _projects_update: Literal["projects/update"]
) -> str | web.WerkzeugResponse | tuple[Mapping[str, Any], int]:
    """
    URL: GET /projects/update

    Update projects from remote data.
    """
    rendered_form = await form.render(
        model_cls=form.Empty,
        submit_label="Update projects",
        empty=True,
        form_classes="",
    )
    return await template.render("update-projects.html", empty_form=rendered_form)


@admin.typed
async def projects_update_post(
    session: web.Committer, _projects_update: Literal["projects/update"], _form: form.Empty
) -> str | web.WerkzeugResponse | tuple[Mapping[str, Any], int]:
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


@admin.typed
async def raise_error(_session: web.Committer, _raise_error: Literal["raise-error"]) -> web.QuartResponse:
    """
    URL: GET /raise-error
    """
    raise RuntimeError("Admin test route deliberately raised an unhandled error")


@admin.typed
async def revoke_user_ssh_keys_get(
    _session: web.Committer, _revoke_user_ssh_keys: Literal["revoke-user-ssh-keys"]
) -> str:
    """
    URL: GET /revoke-user-ssh-keys

    Revoke all SSH keys for a specified user.
    """
    via = sql.validate_instrumented_attribute
    ssh_key_counts: list[tuple[str, int]] = []
    workflow_key_counts: list[tuple[str, int]] = []
    async with db.session() as data:
        ssh_stmt = (
            sqlmodel.select(
                sql.SSHKey.asf_uid,
                sqlmodel.func.count(),
            )
            .group_by(sql.SSHKey.asf_uid)
            .order_by(sql.SSHKey.asf_uid)
        )
        ssh_rows = await data.execute_query(ssh_stmt)
        ssh_key_counts = [(row[0], row[1]) for row in ssh_rows]

        workflow_stmt = (
            sqlmodel.select(
                sql.WorkflowSSHKey.asf_uid,
                sqlmodel.func.count(),
            )
            .where(
                via(sql.WorkflowSSHKey.revoked).is_(False),
                sql.WorkflowSSHKey.expires > int(time.time()),
            )
            .group_by(sql.WorkflowSSHKey.asf_uid)
            .order_by(sql.WorkflowSSHKey.asf_uid)
        )
        workflow_rows = await data.execute_query(workflow_stmt)
        workflow_key_counts = [(row[0], row[1]) for row in workflow_rows]

    rendered_form = await form.render(
        model_cls=RevokeUserSSHKeysForm,
        submit_label="Revoke all SSH keys",
    )
    return await template.render(
        "revoke-user-ssh-keys.html",
        form=rendered_form,
        ssh_key_counts=ssh_key_counts,
        workflow_key_counts=workflow_key_counts,
    )


@admin.typed
async def revoke_user_ssh_keys_post(
    session: web.Committer,
    _revoke_user_ssh_keys: Literal["revoke-user-ssh-keys"],
    revoke_form: RevokeUserSSHKeysForm,
) -> str | web.WerkzeugResponse:
    """
    URL: POST /revoke-user-ssh-keys

    Revoke all SSH keys for a specified user.
    """
    target_uid = revoke_form.asf_uid

    async with storage.write(session) as write:
        wafa = write.as_foundation_admin()
        persistent_count, workflow_count = await wafa.ssh.revoke_all_user_keys(target_uid)

    total = persistent_count + workflow_count
    if total > 0:
        parts = []
        if persistent_count > 0:
            parts.append(util.plural(persistent_count, "persistent key"))
        if workflow_count > 0:
            parts.append(util.plural(workflow_count, "workflow key"))
        await quart.flash(f"Revoked {' and '.join(parts)} for {target_uid}.", "success")
    else:
        await quart.flash(f"No SSH keys found for {target_uid}.", "info")

    return await session.redirect(revoke_user_ssh_keys_get)


@admin.typed
async def revoke_user_tokens_get(_session: web.Committer, _revoke_user_tokens: Literal["revoke-user-tokens"]) -> str:
    """
    URL: GET /revoke-user-tokens

    Revoke all Personal Access Tokens for a specified user.
    """
    # (uid, owned_count, minted_count). owned counts user PATs the uid holds;
    # minted counts system PATs the uid created. Both are revoked by the form
    # below, so they need to appear in the same view.
    token_counts: list[tuple[str, int, int]] = []
    async with db.session() as data:
        via = sql.validate_instrumented_attribute
        owned_stmt = (
            sqlmodel.select(sql.PersonalAccessToken.asfuid, sqlmodel.func.count())
            .where(via(sql.PersonalAccessToken.asfuid).is_not(None))
            .group_by(sql.PersonalAccessToken.asfuid)
        )
        minted_stmt = (
            sqlmodel.select(sql.PersonalAccessToken.created_by, sqlmodel.func.count())
            .where(via(sql.PersonalAccessToken.is_system).is_(True))
            .group_by(sql.PersonalAccessToken.created_by)
        )
        owned_rows = await data.execute_query(owned_stmt)
        minted_rows = await data.execute_query(minted_stmt)

        owned = {row[0]: row[1] for row in owned_rows}
        minted = {row[0]: row[1] for row in minted_rows}
        token_counts = sorted(
            ((uid, owned.get(uid, 0), minted.get(uid, 0)) for uid in (owned.keys() | minted.keys())),
            key=lambda row: row[0],
        )

    rendered_form = await form.render(
        model_cls=RevokeUserTokensForm,
        submit_label="Revoke all tokens",
    )
    return await template.render(
        "revoke-user-tokens.html",
        form=rendered_form,
        token_counts=token_counts,
    )


@admin.typed
async def revoke_user_tokens_post(
    session: web.Committer, _revoke_user_tokens: Literal["revoke-user-tokens"], revoke_form: RevokeUserTokensForm
) -> str | web.WerkzeugResponse:
    """
    URL: POST /revoke-user-tokens

    Revoke all Personal Access Tokens for a specified user.
    """
    # audit_guidance PAT revocation does not terminate web sessions
    # audit_guidance PATs and OAuth sessions are independent auth paths
    target_uid = revoke_form.asf_uid

    async with storage.write(session) as write:
        wafa = write.as_foundation_admin()
        count = await wafa.tokens.revoke_all_user_tokens(target_uid)

    if count > 0:
        await quart.flash(f"Revoked {util.plural(count, 'token')} for {target_uid}.", "success")
    else:
        await quart.flash(f"No tokens found for {target_uid}.", "info")

    return await session.redirect(revoke_user_tokens_get)


@admin.typed
async def system_tokens_get(session: web.Committer, _system_tokens: Literal["system-tokens"]) -> str:
    """
    URL: GET /system-tokens

    Mint and manage system tokens.
    """
    async with storage.write(session) as write:
        wafa = write.as_foundation_admin()
        tokens = await wafa.tokens.list_system_tokens()

    create_form = await form.render(
        model_cls=CreateSystemTokenForm,
        action=util.as_url(system_tokens_create_post),
        submit_label="Create system token",
    )
    rows = []
    for token in tokens:
        revoke_form = await form.render(
            model_cls=RevokeSystemTokenForm,
            action=util.as_url(system_tokens_revoke_post),
            form_classes=".mb-0",
            submit_classes="btn-sm btn-danger",
            submit_label="Revoke",
            confirm="Revoke this system token? Any JWTs issued from it stop working immediately.",
            defaults={"token_id": token.id},
            empty=True,
        )
        rows.append((token, revoke_form))
    return await template.render(
        "system-tokens.html",
        create_form=create_form,
        rows=rows,
        format_datetime=util.format_datetime,
    )


@admin.typed
async def system_tokens_create_post(
    session: web.Committer,
    _system_tokens_create: Literal["system-tokens/create"],
    create_form: CreateSystemTokenForm,
) -> web.WerkzeugResponse:
    """
    URL: POST /system-tokens/create

    Mint a system token and show its secret once.
    """
    plaintext = noisy.create(shared.tokens.PAT_NOISY_SECRET_DOMAIN).decode("ascii")
    token_hash = hashlib.sha3_256(plaintext.encode()).hexdigest()
    created = datetime.datetime.now(datetime.UTC)
    expires = created + datetime.timedelta(days=shared.tokens.PAT_EXPIRY_DAYS)
    allowed_ip = create_form.allowed_ip.strip() or None

    async with storage.write(session) as write:
        wafa = write.as_foundation_admin()
        await wafa.tokens.add_system_token(token_hash, created, expires, create_form.label, allowed_ip)

    await web.flash_success(
        htm.p[
            htm.strong["New system token"],
            " (",
            htm.code[create_form.label],
            "): ",
            htm.code(".bg-light.border.rounded.px-1.text-break")[plaintext],
        ],
        htm.p(".mb-0")["Copy it now - it will not be shown again."],
    )
    return await session.redirect(system_tokens_get)


@admin.typed
async def system_tokens_revoke_post(
    session: web.Committer,
    _system_tokens_revoke: Literal["system-tokens/revoke"],
    revoke_form: RevokeSystemTokenForm,
) -> web.WerkzeugResponse:
    """
    URL: POST /system-tokens/revoke

    Revoke a single system token.
    """
    async with storage.write(session) as write:
        wafa = write.as_foundation_admin()
        revoked = await wafa.tokens.revoke_system_token(revoke_form.token_id)

    if revoked:
        await quart.flash("System token revoked.", "success")
    else:
        await quart.flash("System token not found.", "info")
    return await session.redirect(system_tokens_get)


@admin.typed
async def rotate_jwt_key_get(_session: web.Committer, _rotate_jwt_key: Literal["rotate-jwt-key"]) -> str:
    """
    URL: GET /rotate-jwt-key
    """
    rendered_form = await form.render(
        model_cls=RotateJwtKeyForm,
        submit_label="Rotate JWT key",
    )
    return await _rotate_jwt_key_page(rendered_form)


@admin.typed
async def rotate_jwt_key_post(
    session: web.Committer, _rotate_jwt_key: Literal["rotate-jwt-key"], _rotate_form: RotateJwtKeyForm
) -> str | web.WerkzeugResponse:
    """
    URL: POST /rotate-jwt-key
    """
    async with storage.write(session) as write:
        wafa = write.as_foundation_admin()
        await wafa.tokens.rotate_jwt_signing_key()
    await quart.flash("Rotated the JWT signing key. All existing JWTs are now invalid.", "success")
    return await session.redirect(rotate_jwt_key_get)


@admin.typed
async def task_times(
    _session: web.Committer,
    _task_times: Literal["task-times"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
) -> web.QuartResponse:
    """
    URL: GET /task-times/<project_key>/<version_key>/<revision_number>
    """
    values = []
    async with db.session() as data:
        tasks = await data.task(
            project_key=str(project_key),
            version_key=str(version_key),
            revision_number=str(revision_number),
        ).all()
        for task in tasks:
            if (task.started is None) or (task.completed is None):
                continue
            ms_elapsed = (task.completed - task.started).total_seconds() * 1000
            values.append(f"{task.task_type} {ms_elapsed:.2f}ms")

    return web.TextResponse("\n".join(values))


@admin.typed
async def tasks_list(
    _session: web.Committer,
    _tasks_list: Literal["tasks/list"],
    query_args: api.TasksListQuery,
) -> tuple[dict[str, Any], int]:
    """
    URL: GET /tasks/list

    List tasks.
    """
    try:
        validation.pagination_args_validate(query_args)
    except ValueError as e:
        raise exceptions.BadRequest(str(e))
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        statement = sqlmodel.select(sql.Task).limit(query_args.limit).offset(query_args.offset)
        if query_args.status:
            if query_args.status not in sql.TaskStatus:
                raise exceptions.BadRequest(f"Invalid status: {query_args.status}")
            statement = statement.where(sql.Task.status == query_args.status)
        statement = statement.order_by(via(sql.Task.id).desc())
        paged_tasks = (await data.execute(statement)).scalars().all()
        count_statement = sqlalchemy.select(sqlalchemy.func.count(via(sql.Task.id)))
        if query_args.status:
            count_statement = count_statement.where(via(sql.Task.status) == query_args.status)
        count = (await data.execute(count_statement)).scalar_one()
    return api.TasksListResults(
        endpoint="/admin/tasks/list",
        data=paged_tasks,
        count=count,
    ).model_dump(mode="json"), 200


@admin.typed
async def tasks_recent(_session: web.Committer, _tasks_recent: Literal["tasks/recent"], minutes: int) -> str:
    """
    URL: GET /tasks/recent/<int:minutes>

    Display tasks from the last N minutes.
    """
    if minutes < 1:
        minutes = 1
    if minutes > 1440:
        minutes = 1440

    via = sql.validate_instrumented_attribute
    now = datetime.datetime.now(datetime.UTC)
    cutoff = now - datetime.timedelta(minutes=minutes)

    async with db.session() as data:
        statement = (
            sqlmodel.select(sql.Task)
            .where(
                via(sql.Task.added) >= cutoff,
                sqlalchemy.or_(sqlalchemy.not_(via(sql.Task.scheduled) > now), via(sql.Task.scheduled).is_(None)),
            )
            .order_by(via(sql.Task.added).desc())
        )
        recent_tasks = (await data.execute(statement)).scalars().all()
        scheduled_stmt = (
            sqlmodel.select(sql.Task)
            .where(via(sql.Task.scheduled) > now, via(sql.Task.status) == sql.TaskStatus.QUEUED)
            .order_by(via(sql.Task.scheduled).asc())
        )
        scheduled_tasks = (await data.execute(scheduled_stmt)).scalars().all()

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
                    htpy.td[task.project_key or ""],
                    htpy.td[task.version_key or ""],
                    htpy.td[task.revision_number or ""],
                    htpy.td[error_text],
                ]
            )
        table.append(tbody.collect())
        page.append(table.collect())

    page.h2["Scheduled tasks"]
    page.p[f"Found {util.plural(len(scheduled_tasks), 'task')}"]

    if recent_tasks:
        table = htm.Block(htpy.table, classes=".table.table-sm")
        table.thead(".table-dark")[
            htpy.tr[
                htpy.th["ID"],
                htpy.th["Type"],
                htpy.th["Added"],
                htpy.th["Scheduled"],
                htpy.th["Project"],
                htpy.th["Version"],
                htpy.th["Revision"],
            ]
        ]
        tbody = htm.Block(htpy.tbody)
        for task in scheduled_tasks:
            tbody.append(
                htpy.tr(".table-secondary")[
                    htpy.td[str(task.id)],
                    htpy.td[task.task_type.value],
                    htpy.td[task.added.strftime("%Y-%m-%d %H:%M:%S") if task.added else ""],
                    htpy.td[task.scheduled.strftime("%Y-%m-%d %H:%M:%S") if task.scheduled else ""],
                    htpy.td[task.project_key or ""],
                    htpy.td[task.version_key or ""],
                    htpy.td[task.revision_number or ""],
                ]
            )
        table.append(tbody.collect())
        page.append(table.collect())

    return await template.blank(f"Recent Tasks ({minutes}m)", content=page.collect())


@admin.typed
async def toggle_view_get(_session: web.Committer, _toggle_view: Literal["toggle-view"]) -> str:
    """
    URL: GET /toggle-view

    Display the page with a button to toggle between admin and user views.
    """
    rendered_form = await form.render(
        model_cls=form.Empty,
        submit_label="Toggle view",
        empty=True,
        form_classes=".mb-4",
    )
    return await template.render("toggle-admin-view.html", empty_form=rendered_form)


@admin.typed
async def toggle_view_post(
    session: web.Committer, _toggle_view: Literal["toggle-view"], _form: form.Empty
) -> web.WerkzeugResponse:
    """
    URL: POST /toggle-view

    Toggle between admin and user views.
    """
    session.session.downgrade_admin_to_user = not session.session.downgrade_admin_to_user
    await asfquart.APP.sessions.save(session.session, {"downgrade_admin_to_user"})
    quart.g.is_session_downgraded = session.session.downgrade_admin_to_user

    message = "Viewing as regular user" if session.session.downgrade_admin_to_user else "Viewing as admin"
    await quart.flash(message, "success")
    referrer = quart.request.referrer
    if referrer and web.valid_url(referrer, quart.request.host):
        return quart.redirect(referrer)
    return quart.redirect("https://" + quart.request.host + "/")


@admin.typed
async def validate_(_session: web.Committer, _validate: Literal["validate"]) -> str:
    """
    URL: GET /validate

    Run validators and display any divergences.
    """

    async with db.session() as data:
        divergences = [d async for d in validate.everything(data)]

    return await template.render(
        "validation.html",
        divergences=divergences,
    )


@admin.typed
async def validate_jwt_get(_session: web.Committer, _validate_jwt: Literal["validate-jwt"]) -> str:
    """
    URL: GET /validate-jwt
    """
    _require_non_production_mode()
    rendered_form = await form.render(
        model_cls=ValidateJwtForm,
        submit_label="Validate JWT",
        textarea_rows=8,
    )
    return await _validate_jwt_page(rendered_form, result=None)


@admin.typed
async def validate_jwt_post(
    _session: web.Committer, _validate_jwt: Literal["validate-jwt"], validate_form: ValidateJwtForm
) -> str:
    """
    URL: POST /validate-jwt
    """
    _require_non_production_mode()
    token = validate_form.token
    result: dict[str, Any] = {"token_length": len(token), "valid": False}

    try:
        result["header"] = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        result["header_error"] = f"{type(exc).__name__}: {exc}"

    try:
        result["claims_unverified"] = jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        result["claims_unverified_error"] = f"{type(exc).__name__}: {exc}"

    try:
        result["claims_verified"] = await jwtoken.verify(token)
        result["valid"] = True
    except Exception as exc:
        result["validation_error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }

    rendered_form = await form.render(
        model_cls=ValidateJwtForm,
        submit_label="Validate JWT",
        textarea_rows=8,
        defaults={"token": token},
    )
    return await _validate_jwt_page(rendered_form, result=result)


async def _check_keys(fix: bool = False) -> str:
    email_uid_lookup = await cache.email_uid_view_or_live()
    bad_keys = []
    async with db.session() as data:
        keys = await data.public_signing_key().all()
        for key in keys:
            uids = []
            if key.primary_declared_uid:
                uids.append(key.primary_declared_uid)
            if key.secondary_declared_uids:
                uids.extend(key.secondary_declared_uids)
            asf_uid = await util.asf_uid_from_uids(uids, email_uid_lookup, use_ldap=False)
            if asf_uid != key.apache_uid:
                bad_keys.append(f"{key.fingerprint} detected: {asf_uid}, key: {key.apache_uid}")
            if fix:
                key.apache_uid = asf_uid
                await data.commit()
    message = f"Checked {util.plural(len(keys), 'key')}"
    if bad_keys:
        message += f"\nFound {util.plural(len(bad_keys), 'bad key')}:\n{'\n'.join(bad_keys)}"
    return message


async def _data_browse(_session: web.Committer, model: str = "Committee") -> str:
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

    for release_key in releases_to_delete:
        try:
            async with db.session() as data:
                release = await data.release(key=release_key, _committee=True, _project=True).demand(
                    RuntimeError(f"Release {release_key} not found")
                )
                if release.committee is None:
                    raise RuntimeError(f"Release {release_key} has no committee")
            async with storage.write(session) as write:
                waca = write.as_committee_admin(release.committee.key)
                error = await waca.release.delete(release.safe_project_key, release.safe_version_key)
                # Ensure that deletion errors are reported to the user
                if error is not None:
                    raise RuntimeError(error)
            success_count += 1
        except base.ASFQuartException as e:
            log.error(f"Error deleting release {release_key}: {e}")
            fail_count += 1
            error_messages.append(f"{release_key}: {e}")
        except Exception as e:
            log.exception(f"Unexpected error deleting release {release_key}:")
            fail_count += 1
            error_messages.append(f"{release_key}: Unexpected error ({e})")

    if success_count > 0:
        await quart.flash(f"Successfully deleted {util.plural(success_count, 'release')}.", "success")
    if fail_count > 0:
        errors_str = "\n".join(error_messages)
        await quart.flash(f"Failed to delete {util.plural(fail_count, 'release')}:\n{errors_str}", "error")


async def _fetch_ongoing_tasks(
    _session: web.Committer,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber,
) -> web.QuartResponse:
    try:
        ongoing = await interaction.tasks_ongoing(project_key, version_key, revision)
        return web.TextResponse(str(ongoing))
    except Exception:
        log.exception(f"Error fetching ongoing task count for {project_key!s} {version_key!s} rev {revision!s}:")
        return web.TextResponse("")


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
    finished_dir = paths.get_finished_dir()
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
    unfinished_dir = paths.get_unfinished_dir()
    unfinished_dir_contents = await aiofiles.os.listdir(unfinished_dir)
    for project_dir in unfinished_dir_contents:
        project_dir_path = os.path.join(unfinished_dir, project_dir)
        if await aiofiles.os.path.isdir(project_dir_path):
            for version_dir in await aiofiles.os.listdir(project_dir_path):
                if await aiofiles.os.path.isdir(os.path.join(project_dir_path, version_dir)):
                    version_dir_path = os.path.join(project_dir_path, version_dir)
                    if await aiofiles.os.path.isdir(version_dir_path):
                        filesystem_dirs.append(version_dir_path)


def _release_age_row(release: sql.Release, now: datetime.datetime) -> ReleaseAgeRow:
    age = now - release.created
    inactive_days = release.days_since_active
    return ReleaseAgeRow(
        release=release,
        age_label=util.plural(age.days, "day"),
        age_bold=age > _MAXIMUM_PERMITTED_AGE_DAYS,
        inactive_label=util.plural(inactive_days, "day"),
        inactive_bold=inactive_days > _MAXIMUM_PERMITTED_AGE_DAYS.days,
    )


def _require_non_production_mode() -> None:
    if config.is_production_mode():
        quart.abort(404)


async def _rotate_jwt_key_page(rendered_form: htm.Element) -> str:
    page = htm.Block()
    page.h1["Rotate JWT key"]
    page.p["Rotate the JWT signing key immediately. This will invalidate all currently usable JWTs."]

    page.append(rendered_form)

    return await template.blank(
        title="Rotate JWT key",
        content=page.collect(),
    )


async def _update_keys(asf_uid: str) -> int:
    async def _log_process(process: asyncio.subprocess.Process) -> None:
        try:
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                log.error(f"keys_import.py exited with code {process.returncode}")
            else:
                log.info(f"keys_import.py exited with code {process.returncode}")
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
        command = ["uv", "run", "--frozen", "python3", "scripts/keys_import.py", asf_uid]
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


async def _validate_jwt_page(rendered_form: htm.Element, result: dict[str, Any] | None) -> str:
    page = htm.Block()
    page.h1["Validate JWT"]
    page.p["Paste a JWT below to inspect unverified details and full verification results."]

    page.append(rendered_form)

    if result is not None:
        page.h2["Result"]
        result_text = json.dumps(result, indent=2, sort_keys=True, default=str)
        page.pre[htm.code[result_text]]

    return await template.blank(
        title="Validate JWT",
        content=page.collect(),
    )
