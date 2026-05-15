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
import contextlib
import datetime
import hashlib
import os
import pathlib
import uuid
from collections.abc import Iterable, Mapping
from typing import Final

import aiofiles
import aiofiles.os
import asfquart
import pydantic

import atr.config as config
import atr.ldap as ldap
import atr.log as log
import atr.models.safe as safe
import atr.models.schema as schema

# Fifth prime after 3600
ADMINS_POLL_INTERVAL_SECONDS: Final[int] = 3631

PROJECT_VERSION_POLL_INTERVAL_SECONDS: Final[int] = 307


class AdminsCache(schema.Strict):
    refreshed: datetime.datetime = schema.description("When the cache was last refreshed")
    admins: frozenset[str] = schema.description("Set of admin user IDs from LDAP")


class EmailUidCache(schema.Strict):
    refreshed: datetime.datetime = schema.description("When the cache was last refreshed")
    hashes: dict[str, str] = schema.description("SHA-256(email.lower()) to ASF uid")
    reverse: dict[str, list[str]] = schema.description("ASF uid to list of SHA-256 email hashes")


class EmailUidLookup:
    def __init__(self, hashes: Mapping[str, str]) -> None:
        self._hashes = hashes

    @classmethod
    def from_plain(cls, mapping: Mapping[str, str]) -> "EmailUidLookup":
        return cls({_email_uid_hash(email): uid for email, uid in mapping.items() if email})

    def get(self, email: str) -> str | None:
        if not email:
            return None
        return self._hashes.get(_email_uid_hash(email))

    def __contains__(self, email: object) -> bool:
        if not isinstance(email, str):
            return False
        if not email:
            return False
        return _email_uid_hash(email) in self._hashes

    def __getitem__(self, email: str) -> str:
        return self._hashes[_email_uid_hash(email)]

    def __len__(self) -> int:
        return len(self._hashes)


def admins_get() -> frozenset[str]:
    if asfquart.APP is not None:
        return asfquart.APP.extensions.get("admins", frozenset())
    cache_data = _admins_read_from_file()
    if cache_data is None:
        return frozenset()
    return cache_data.admins


async def admins_get_async() -> frozenset[str]:
    if asfquart.APP is not None:
        return asfquart.APP.extensions.get("admins", frozenset())
    cache_data = await _admins_read_from_file_async()
    if cache_data is None:
        return frozenset()
    return cache_data.admins


async def admins_refresh_loop() -> None:
    while True:
        await asyncio.sleep(ADMINS_POLL_INTERVAL_SECONDS)
        try:
            users = await ldap.fetch_admin_users()
            await admins_save_to_file(users)
            _admins_update_app_extensions(users)
            log.info(f"Admin users cache refreshed: {len(users)} users")
        except Exception as e:
            log.warning(f"Admin refresh failed: {e}")


async def admins_save_to_file(admins: frozenset[str]) -> None:
    cache_path = _admins_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_data = AdminsCache(refreshed=datetime.datetime.now(datetime.UTC), admins=admins)
    async with aiofiles.open(cache_path, "w") as f:
        await f.write(cache_data.model_dump_json())


async def admins_startup_load() -> None:
    cache_data = await _admins_read_from_file_async()
    if cache_data is not None:
        _admins_update_app_extensions(cache_data.admins)
        log.info(f"Loaded {len(cache_data.admins)} admin users from cache (refreshed: {cache_data.refreshed})")
        return
    log.info("No admin users cache found, fetching from LDAP")
    try:
        users = await ldap.fetch_admin_users()
        await admins_save_to_file(users)
        _admins_update_app_extensions(users)
        log.info(f"Fetched {len(users)} admin users from LDAP")
    except Exception as e:
        log.warning(f"Failed to fetch admin users from LDAP at startup: {e}")


def email_uid_apply_delta(uid: str, old_emails: Iterable[str], new_emails: Iterable[str]) -> bool:
    if asfquart.APP is None:
        return False
    hashes = asfquart.APP.extensions.get("email_uid_hashes")
    reverse = asfquart.APP.extensions.get("email_uid_reverse")
    if (not isinstance(hashes, dict)) or (not isinstance(reverse, dict)):
        return False
    uid_lower = uid.lower()
    old_set = {e.lower() for e in old_emails if e}
    new_set = {e.lower() for e in new_emails if e}
    removed = old_set - new_set
    added = new_set - old_set
    if (not removed) and (not added):
        return False
    existing = set(reverse.get(uid_lower, []))
    for email in removed:
        h = _email_uid_hash(email)
        if hashes.get(h) == uid_lower:
            del hashes[h]
        existing.discard(h)
    for email in added:
        h = _email_uid_hash(email)
        hashes[h] = uid_lower
        existing.add(h)
    if existing:
        reverse[uid_lower] = sorted(existing)
    elif uid_lower in reverse:
        del reverse[uid_lower]
    asfquart.APP.extensions["email_uid_refreshed"] = datetime.datetime.now(datetime.UTC)
    return True


def email_uid_erase() -> None:
    cache_path = _email_uid_path()
    try:
        cache_path.unlink(missing_ok=True)
    except OSError as e:
        log.warning(f"Failed to erase email-to-UID cache: {e}")


def email_uid_lookup(email: str) -> str | None:
    return _email_uid_view().get(email)


def email_uid_purge_uid(uid: str) -> bool:
    if asfquart.APP is None:
        return False
    hashes = asfquart.APP.extensions.get("email_uid_hashes")
    reverse = asfquart.APP.extensions.get("email_uid_reverse")
    if (not isinstance(hashes, dict)) or (not isinstance(reverse, dict)):
        return False
    uid_lower = uid.lower()
    uid_hashes = reverse.pop(uid_lower, [])
    if not uid_hashes:
        return False
    for h in uid_hashes:
        if hashes.get(h) == uid_lower:
            del hashes[h]
    asfquart.APP.extensions["email_uid_refreshed"] = datetime.datetime.now(datetime.UTC)
    return True


async def email_uid_refresh() -> None:
    import atr.util as util

    email_to_uid = await util.email_to_uid_map()
    hashes: dict[str, str] = {}
    reverse: dict[str, list[str]] = {}
    for email, uid in email_to_uid.items():
        if (not email) or (not uid):
            continue
        h = _email_uid_hash(email)
        hashes[h] = uid
        reverse.setdefault(uid, []).append(h)
    if (not hashes) or (not reverse):
        log.warning(
            "Email-to-UID cache refresh produced no usable LDAP email mappings; not writing cache: "
            f"ldap_email_values={len(email_to_uid)}"
        )
        raise RuntimeError("Email-to-UID cache refresh produced no usable LDAP email mappings")
    await email_uid_save_to_file(hashes, reverse)
    _email_uid_update_app_extensions(hashes, reverse)
    log.info(f"Email-to-UID cache refreshed: {len(hashes)} hashes for {len(reverse)} users")


async def email_uid_save_current_to_file() -> None:
    if asfquart.APP is None:
        return
    hashes = asfquart.APP.extensions.get("email_uid_hashes")
    reverse = asfquart.APP.extensions.get("email_uid_reverse")
    if (not isinstance(hashes, dict)) or (not isinstance(reverse, dict)):
        return
    hashes_copy = dict(hashes)
    reverse_copy = {uid: list(hs) for uid, hs in reverse.items()}
    await email_uid_save_to_file(hashes_copy, reverse_copy)
    _email_uid_update_app_extensions(hashes_copy, reverse_copy)


async def email_uid_save_to_file(hashes: dict[str, str], reverse: dict[str, list[str]]) -> None:
    cache_path = _email_uid_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_data = EmailUidCache(
        refreshed=datetime.datetime.now(datetime.UTC),
        hashes=hashes,
        reverse=reverse,
    )
    content = cache_data.model_dump_json()
    temp_path = cache_path.parent / f".{cache_path.name}.{uuid.uuid4()}.tmp"
    try:
        async with aiofiles.open(temp_path, "w") as f:
            await f.write(content)
            await f.flush()
            await asyncio.to_thread(os.fsync, f.fileno())
        await asyncio.to_thread(os.chmod, temp_path, 0o400)
        await aiofiles.os.rename(temp_path, cache_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            await aiofiles.os.remove(temp_path)
        raise


async def email_uid_startup_load() -> None:
    log.info("Fetching email-to-UID cache from LDAP at startup")
    try:
        await email_uid_refresh()
    except Exception as e:
        log.warning(f"Failed to populate email-to-UID cache at startup from LDAP: {e}")


def email_uid_view() -> EmailUidLookup:
    return _email_uid_view()


async def email_uid_view_or_live() -> EmailUidLookup:
    view = _email_uid_view()
    if len(view) > 0:
        return view
    log.warning("Email-to-UID cache empty, falling back to live LDAP query")
    await email_uid_refresh()
    return _email_uid_view()


def project_version_get() -> dict[str, set[str]]:
    if asfquart.APP is not None:
        return asfquart.APP.extensions.get("project_versions", {})
    return {}


def project_version_has_project(project_key: str) -> bool:
    return project_key in project_version_get()


def project_version_has_version(project_key: safe.ProjectKey, version_key: str) -> bool:
    projects = project_version_get()
    if str(project_key) not in projects:
        return False
    return version_key in projects[str(project_key)]


async def project_version_refresh_loop() -> None:
    while True:
        await asyncio.sleep(PROJECT_VERSION_POLL_INTERVAL_SECONDS)
        try:
            await _project_version_refresh()
        except Exception as e:
            log.warning(f"Project/version cache refresh failed: {e}")


async def project_version_startup_load() -> None:
    try:
        await _project_version_refresh()
    except Exception as e:
        log.warning(f"Failed to populate project/version cache at startup: {e}")


def _admins_path() -> pathlib.Path:
    return pathlib.Path(config.get().STATE_DIR) / "cache" / "admins.json"


def _admins_read_from_file() -> AdminsCache | None:
    cache_path = _admins_path()
    if not cache_path.exists():
        return None
    try:
        with open(cache_path) as f:
            raw_data = f.read()
    except OSError as e:
        log.warning(f"Failed to read admin users cache: {e}")
        return None
    try:
        return AdminsCache.model_validate_json(raw_data)
    except pydantic.ValidationError as e:
        log.warning(f"Failed to read admin users cache: {e}")
        return None


async def _admins_read_from_file_async() -> AdminsCache | None:
    cache_path = _admins_path()
    if not cache_path.exists():
        return None
    try:
        async with aiofiles.open(cache_path) as f:
            raw_data = await f.read()
    except OSError as e:
        log.warning(f"Failed to read admin users cache: {e}")
        return None
    try:
        return AdminsCache.model_validate_json(raw_data)
    except pydantic.ValidationError as e:
        log.warning(f"Failed to read admin users cache: {e}")
        return None


def _admins_update_app_extensions(admins: frozenset[str]) -> None:
    app = asfquart.APP
    app.extensions["admins"] = admins
    app.extensions["admins_refreshed"] = datetime.datetime.now(datetime.UTC)


def _email_uid_file_mtime_ns() -> int | None:
    try:
        return _email_uid_path().stat().st_mtime_ns
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning(f"Failed to stat email-to-UID cache: {e}")
        return None


def _email_uid_hash(email: str) -> str:
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()


def _email_uid_path() -> pathlib.Path:
    return pathlib.Path(config.get().STATE_DIR) / "secrets" / "cached" / "email_uid.json"


def _email_uid_read_from_file() -> EmailUidCache | None:
    cache_path = _email_uid_path()
    if not cache_path.exists():
        return None
    try:
        with open(cache_path) as f:
            raw_data = f.read()
    except OSError as e:
        log.warning(f"Failed to read email-to-UID cache: {e}")
        return None
    try:
        cache_data = EmailUidCache.model_validate_json(raw_data)
    except pydantic.ValidationError as e:
        log.warning(f"Failed to parse email-to-UID cache: {e}")
        return None
    return _email_uid_validate_cache_data(cache_path, cache_data)


async def _email_uid_read_from_file_async() -> EmailUidCache | None:
    cache_path = _email_uid_path()
    if not cache_path.exists():
        return None
    try:
        async with aiofiles.open(cache_path) as f:
            raw_data = await f.read()
    except OSError as e:
        log.warning(f"Failed to read email-to-UID cache: {e}")
        return None
    try:
        cache_data = EmailUidCache.model_validate_json(raw_data)
    except pydantic.ValidationError as e:
        log.warning(f"Failed to parse email-to-UID cache: {e}")
        return None
    return _email_uid_validate_cache_data(cache_path, cache_data)


def _email_uid_reload_app_extensions_from_file_if_changed() -> None:
    app = asfquart.APP
    if app is None:
        return
    file_mtime_ns = _email_uid_file_mtime_ns()
    if file_mtime_ns is None:
        return
    stored_mtime_ns = app.extensions.get("email_uid_file_mtime_ns")
    hashes = app.extensions.get("email_uid_hashes")
    if (stored_mtime_ns == file_mtime_ns) and isinstance(hashes, dict):
        return
    cache_data = _email_uid_read_from_file()
    if cache_data is None:
        return
    _email_uid_update_app_extensions(cache_data.hashes, cache_data.reverse, file_mtime_ns)


def _email_uid_update_app_extensions(
    hashes: dict[str, str], reverse: dict[str, list[str]], file_mtime_ns: int | None = None
) -> None:
    app = asfquart.APP
    if app is None:
        return
    app.extensions["email_uid_hashes"] = dict(hashes)
    app.extensions["email_uid_reverse"] = {uid: list(hs) for uid, hs in reverse.items()}
    app.extensions["email_uid_refreshed"] = datetime.datetime.now(datetime.UTC)
    mtime_ns = file_mtime_ns
    if mtime_ns is None:
        mtime_ns = _email_uid_file_mtime_ns()
    app.extensions["email_uid_file_mtime_ns"] = mtime_ns


def _email_uid_validate_cache_data(cache_path: pathlib.Path, cache_data: EmailUidCache) -> EmailUidCache | None:
    if cache_data.hashes and cache_data.reverse:
        return cache_data
    log.warning(f"Ignoring empty email-to-UID cache at {cache_path} (refreshed: {cache_data.refreshed})")
    try:
        cache_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning(f"Failed to remove empty email-to-UID cache at {cache_path}: {e}")
    return None


def _email_uid_view() -> EmailUidLookup:
    if asfquart.APP is not None:
        _email_uid_reload_app_extensions_from_file_if_changed()
        hashes = asfquart.APP.extensions.get("email_uid_hashes")
        if isinstance(hashes, dict):
            return EmailUidLookup(hashes)
    cache_data = _email_uid_read_from_file()
    if cache_data is None:
        return EmailUidLookup({})
    return EmailUidLookup(cache_data.hashes)


async def _project_version_fetch_from_db() -> dict[str, set[str]]:
    import atr.db as db
    import atr.models.sql as sql

    projects: dict[str, set[str]] = {}
    async with db.session() as data:
        all_projects = await data.project(status=sql.ProjectStatus.ACTIVE, _committee=False).all()
        for project in all_projects:
            all_releases = await data.release(project_key=project.key, _project=False, _committee=False).all()
            projects[project.key] = {release.version for release in all_releases}
    return projects


async def _project_version_refresh() -> None:
    projects = await _project_version_fetch_from_db()
    _project_version_update_app_extensions(projects)
    total_versions = sum(len(v) for v in projects.values())
    log.info(f"Project/version cache refreshed: {len(projects)} projects, {total_versions} versions")


def _project_version_update_app_extensions(projects: dict[str, set[str]]) -> None:
    app = asfquart.APP
    app.extensions["project_versions"] = projects
    app.extensions["project_versions_refreshed"] = datetime.datetime.now(datetime.UTC)
