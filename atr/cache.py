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
import datetime
import pathlib
from typing import Final

import aiofiles
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


def project_version_get() -> dict[str, set[str]]:
    if asfquart.APP is not None:
        return asfquart.APP.extensions.get("project_versions", {})
    return {}


def project_version_has_project(project_name: str) -> bool:
    return project_name in project_version_get()


def project_version_has_version(project_name: safe.ProjectKey, version_name: str) -> bool:
    projects = project_version_get()
    if str(project_name) not in projects:
        return False
    return version_name in projects[str(project_name)]


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
