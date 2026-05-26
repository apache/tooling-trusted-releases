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
import sqlite3
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Final

import sqlalchemy.exc
import sqlmodel

import atr.attestable as attestable
import atr.db as db
import atr.hashes as hashes
import atr.log as log
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as file_paths
import atr.tasks.checks as checks
import atr.tasks.checks.compare as compare
import atr.tasks.checks.hashing as hashing
import atr.tasks.checks.license as license
import atr.tasks.checks.paths as paths
import atr.tasks.checks.rat as rat
import atr.tasks.checks.signature as signature
import atr.tasks.checks.targz as targz
import atr.tasks.checks.zipformat as zipformat
import atr.tasks.distribution as distribution
import atr.tasks.gha as gha
import atr.tasks.keys as keys
import atr.tasks.maintenance as maintenance
import atr.tasks.message as message
import atr.tasks.metadata as metadata
import atr.tasks.quarantine as quarantine
import atr.tasks.sbom as sbom
import atr.tasks.svn as svn
import atr.tasks.svnpub as svnpub
import atr.tasks.vote as vote
import atr.util as util

_EVERY_2_MINUTES = 60 * 2
_DAILY = 60 * 60 * 24


async def asc_checks(
    asf_uid: str, release: sql.Release, revision: safe.RevisionNumber, signature_path: str, data: db.Session
) -> list[sql.Task | None]:
    """Create signature check task for a .asc file."""
    tasks = []

    if release.committee:
        tasks.append(
            await queued(
                asf_uid,
                sql.TaskType.SIGNATURE_CHECK,
                release,
                revision,
                signature_path,
                check_cache_key=await checks.resolve_cache_key(
                    resolve(sql.TaskType.SIGNATURE_CHECK),
                    signature.CHECK_VERSION,
                    signature.INPUT_POLICY_KEYS,
                    release,
                    revision,
                    await checks.resolve_extra_args(signature.INPUT_EXTRA_ARGS, release, signature_path),
                    file=signature_path,
                ),
                extra_args={"committee_key": release.committee.key},
            )
        )

    return tasks


async def clear_scheduled(caller_data: db.Session | None = None) -> None:
    """Clear all future scheduled tasks of the given types."""
    async with db.ensure_session(caller_data) as data:
        via = sql.validate_instrumented_attribute

        delete_stmt = sqlmodel.delete(sql.Task).where(
            via(sql.Task.task_type).in_(
                [
                    sql.TaskType.MAINTENANCE,
                    sql.TaskType.METADATA_UPDATE,
                    sql.TaskType.WORKFLOW_STATUS,
                    sql.TaskType.DISTRIBUTION_STATUS,
                ]
            ),
            via(sql.Task.status) == sql.TaskStatus.QUEUED,
            via(sql.Task.scheduled).is_not(None),
        )

        await data.execute(delete_stmt)
        await data.commit()


async def distribution_status_check(
    asf_uid: str,
    caller_data: db.Session | None = None,
    schedule: datetime.datetime | None = None,
    schedule_next: bool = False,
) -> sql.Task:
    """Queue a workflow status update task."""
    task_args = args.DistributionStatusCheckArgs(next_schedule_seconds=0, asf_uid=asf_uid)
    if schedule_next:
        task_args.next_schedule_seconds = _EVERY_2_MINUTES
    async with db.ensure_session(caller_data) as data:
        task = sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.DISTRIBUTION_STATUS,
            task_args=task_args.model_dump(),
            asf_uid=asf_uid,
            revision_number=None,
            primary_rel_path=None,
        )
        if schedule:
            task.scheduled = schedule
            await data.begin_immediate()
            await _clear_existing_scheduled(data, sql.TaskType.DISTRIBUTION_STATUS, asf_uid)
        data.add(task)
        await data.commit()
        await data.flush()
        return task


async def draft_checks(
    asf_uid: str,
    project_key: safe.ProjectKey,
    release_version: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    caller_data: db.Session | None = None,
    suffix_filter: list[str] | None = None,
) -> int:
    """Core logic to analyse a draft revision and queue checks."""
    # Construct path to the specific revision
    # We don't have the release object here, so we can't use util.release_directory
    revision_path = file_paths.get_unfinished_dir() / str(project_key) / str(release_version) / str(revision_number)
    relative_paths = [path async for path in util.paths_recursive(revision_path)]

    async with db.ensure_session(caller_data) as data:
        release = await data.release(
            key=sql.release_key(str(project_key), str(release_version)),
            _committee=True,
            _release_policy=True,
            _project_release_policy=True,
        ).demand(RuntimeError("Release not found"))
        other_releases = (
            await data.release(project_key=str(project_key), phase=sql.ReleasePhase.RELEASE)
            .order_by(sql.Release.released)
            .all()
        )
        release_versions = sorted(
            [v for v in other_releases], key=lambda v: util.version_sort_key(str(v.version)), reverse=True
        )
        release_version_sortable = util.version_sort_key(str(release_version))
        previous_version = next(
            (v for v in release_versions if util.version_sort_key(str(v.version)) < release_version_sortable), None
        )
        for path in relative_paths:
            if suffix_filter and not str(path).endswith(tuple(suffix_filter)):
                continue
            await _draft_file_checks(
                asf_uid,
                caller_data,
                data,
                path,
                previous_version,
                project_key,
                release,
                release_version,
                revision_number,
            )

        is_podling = False
        if release.project.committee is not None:
            if release.project.committee.is_podling:
                is_podling = True
        path_check_task = await queued(
            asf_uid,
            sql.TaskType.PATHS_CHECK,
            release,
            revision_number,
            check_cache_key=await checks.resolve_cache_key(
                resolve(sql.TaskType.PATHS_CHECK),
                paths.CHECK_VERSION,
                paths.INPUT_POLICY_KEYS,
                release,
                revision_number,
                await checks.resolve_extra_args(paths.INPUT_EXTRA_ARGS, release),
                ignore_path=True,
            ),
            extra_args={"is_podling": is_podling},
        )
        if path_check_task:
            await _add_task(data, path_check_task)
            if caller_data is None:
                await data.commit()

    return len(relative_paths)


async def keys_import_file(
    asf_uid: str,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: str,
    caller_data: db.Session | None = None,
) -> None:
    """Import a KEYS file from a draft release candidate revision."""
    async with db.ensure_session(caller_data) as data:
        data.add(
            sql.Task(
                status=sql.TaskStatus.QUEUED,
                task_type=sql.TaskType.KEYS_IMPORT_FILE,
                task_args=args.ImportFile(
                    asf_uid=asf_uid,
                    project_key=project_key,
                    version_key=version_key,
                ).model_dump(),
                asf_uid=asf_uid,
                revision_number=revision_number,
                primary_rel_path=None,
                project_key=str(project_key),
                version_key=str(version_key),
            )
        )
        await data.commit()


async def metadata_update(
    asf_uid: str,
    caller_data: db.Session | None = None,
    schedule: datetime.datetime | None = None,
    schedule_next: bool = False,
) -> sql.Task:
    """Queue a metadata update task."""
    task_args = args.Update(asf_uid=asf_uid, next_schedule_seconds=0)
    if schedule_next:
        task_args.next_schedule_seconds = _DAILY
    async with db.ensure_session(caller_data) as data:
        task = sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.METADATA_UPDATE,
            task_args=task_args.model_dump(),
            asf_uid=asf_uid,
            revision_number=None,
            primary_rel_path=None,
            project_key=None,
            version_key=None,
        )
        if schedule:
            task.scheduled = schedule
            await data.begin_immediate()
            await _clear_existing_scheduled(data, sql.TaskType.METADATA_UPDATE, asf_uid)
        data.add(task)
        await data.commit()
        await data.flush()
        return task


async def queued(
    asf_uid: str,
    task_type: sql.TaskType,
    release: sql.Release,
    revision_number: safe.RevisionNumber,
    primary_rel_path: str | None = None,
    extra_args: dict[str, Any] | None = None,
    check_cache_key: dict[str, Any] | None = None,
) -> sql.Task | None:
    hash_val: str | None = None
    if check_cache_key is not None:
        if "checker" not in check_cache_key:
            raise ValueError("Cache key must contain a 'checker' key")
        hash_val = hashes.compute_dict_hash(check_cache_key)
        await attestable.write_checks_data(
            release.safe_project_key,
            release.safe_version_key,
            revision_number,
            primary_rel_path or "",
            {check_cache_key["checker"]: hash_val},
        )
    return sql.Task(
        status=sql.TaskStatus.QUEUED,
        task_type=task_type,
        task_args=extra_args or {},
        asf_uid=asf_uid,
        project_key=release.project.key,
        version_key=release.version,
        revision_number=str(revision_number),
        primary_rel_path=primary_rel_path,
        inputs_hash=hash_val,
    )


def resolve(task_type: sql.TaskType) -> Callable[..., Awaitable[results.Results | None]]:  # noqa: C901
    match task_type:
        case sql.TaskType.COMPARE_SOURCE_TREES:
            return compare.source_trees
        case sql.TaskType.DISTRIBUTION_STATUS:
            return distribution.status_check
        case sql.TaskType.DISTRIBUTION_WORKFLOW:
            return gha.trigger_workflow
        case sql.TaskType.HASHING_CHECK:
            return hashing.check
        case sql.TaskType.KEYS_IMPORT_FILE:
            return keys.import_file
        case sql.TaskType.LICENSE_FILES:
            return license.files
        case sql.TaskType.LICENSE_HEADERS:
            return license.headers
        case sql.TaskType.MAINTENANCE:
            return maintenance.run
        case sql.TaskType.MESSAGE_SEND:
            return message.send
        case sql.TaskType.METADATA_UPDATE:
            return metadata.update
        case sql.TaskType.PATHS_CHECK:
            return paths.check
        case sql.TaskType.QUARANTINE_VALIDATE:
            return quarantine.validate
        case sql.TaskType.RAT_CHECK:
            return rat.check
        case sql.TaskType.SBOM_AUGMENT:
            return sbom.augment
        case sql.TaskType.SBOM_CONVERT:
            return sbom.convert_cyclonedx
        case sql.TaskType.SBOM_GENERATE_CYCLONEDX:
            return sbom.generate_cyclonedx
        case sql.TaskType.SBOM_OSV_SCAN:
            return sbom.osv_scan
        case sql.TaskType.SBOM_QS_SCORE:
            return sbom.score_qs
        case sql.TaskType.SBOM_TOOL_SCORE:
            return sbom.score_tool
        case sql.TaskType.SIGNATURE_CHECK:
            return signature.check
        case sql.TaskType.SVN_IMPORT_FILES:
            return svn.import_files
        case sql.TaskType.SVN_PUBLISH:
            return svnpub.publish
        case sql.TaskType.TARGZ_INTEGRITY:
            raise ValueError("TARGZ_INTEGRITY check has been removed; quarantine extraction validates integrity")
        case sql.TaskType.TARGZ_STRUCTURE:
            return targz.structure
        case sql.TaskType.VOTE_AUTO_RESOLVE:
            return vote.auto_resolve
        case sql.TaskType.VOTE_END_NOTIFY:
            return vote.end_notify
        case sql.TaskType.VOTE_INITIATE:
            return vote.initiate
        case sql.TaskType.WORKFLOW_STATUS:
            return gha.status_check
        case sql.TaskType.ZIPFORMAT_INTEGRITY:
            raise ValueError("ZIPFORMAT_INTEGRITY check has been removed; quarantine extraction validates integrity")
        case sql.TaskType.ZIPFORMAT_STRUCTURE:
            return zipformat.structure
        # NOTE: Do NOT add "case _" here
        # Otherwise we lose exhaustiveness checking


async def run_maintenance(
    asf_uid: str,
    caller_data: db.Session | None = None,
    schedule: datetime.datetime | None = None,
    schedule_next: bool = False,
) -> sql.Task:
    """Queue a maintenance task."""
    task_args = args.MaintenanceArgs(asf_uid=asf_uid, next_schedule_seconds=0)
    if schedule_next:
        task_args.next_schedule_seconds = _DAILY
    async with db.ensure_session(caller_data) as data:
        task = sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.MAINTENANCE,
            task_args=task_args.model_dump(),
            asf_uid=asf_uid,
            revision_number=None,
            primary_rel_path=None,
            project_key=None,
            version_key=None,
        )
        if schedule:
            task.scheduled = schedule
        if (schedule is not None) or schedule_next:
            await data.begin_immediate()
            await _clear_existing_scheduled(
                data,
                sql.TaskType.MAINTENANCE,
                asf_uid,
                include_unscheduled=schedule_next,
            )
        data.add(task)
        await data.commit()
        await data.flush()
        return task


async def schedule_next(asf_uid: str, seconds: int, task: Callable[..., Awaitable[sql.Task]]) -> None:
    """Schedule the next run of a recurring task."""
    if seconds > 0:
        next_schedule = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=seconds)
        await task(asf_uid, schedule=next_schedule, schedule_next=True)
        log.info(f"Scheduled next run for: {next_schedule.strftime('%Y-%m-%d %H:%M:%S')}")


async def sha_checks(
    asf_uid: str, release: sql.Release, revision: safe.RevisionNumber, hash_file: str, data: db.Session
) -> list[sql.Task | None]:
    """Create hash check task for a .sha256 or .sha512 file."""
    tasks = []

    tasks.append(
        queued(
            asf_uid,
            sql.TaskType.HASHING_CHECK,
            release,
            revision,
            hash_file,
            check_cache_key=await checks.resolve_cache_key(
                resolve(sql.TaskType.HASHING_CHECK),
                hashing.CHECK_VERSION,
                hashing.INPUT_POLICY_KEYS,
                release,
                revision,
                await checks.resolve_extra_args(hashing.INPUT_EXTRA_ARGS, release, hash_file),
                file=hash_file,
            ),
        )
    )

    return await asyncio.gather(*tasks)


async def tar_gz_checks(
    asf_uid: str, release: sql.Release, revision: safe.RevisionNumber, path: str, data: db.Session
) -> list[sql.Task | None]:
    """Create check tasks for a .tar.gz or .tgz file."""
    # This release has committee, as guaranteed in draft_checks
    is_podling = (release.project.committee is not None) and release.project.committee.is_podling
    compare_ck = await checks.resolve_cache_key(
        resolve(sql.TaskType.COMPARE_SOURCE_TREES),
        compare.CHECK_VERSION,
        compare.INPUT_POLICY_KEYS,
        release,
        revision,
        await checks.resolve_extra_args(compare.INPUT_EXTRA_ARGS, release),
        file=path,
    )
    license_h_ck = await checks.resolve_cache_key(
        resolve(sql.TaskType.LICENSE_HEADERS),
        license.CHECK_VERSION_HEADERS,
        license.INPUT_POLICY_KEYS,
        release,
        revision,
        await checks.resolve_extra_args(license.INPUT_EXTRA_ARGS, release),
        file=path,
    )
    license_f_ck = await checks.resolve_cache_key(
        resolve(sql.TaskType.LICENSE_FILES),
        license.CHECK_VERSION_FILES,
        license.INPUT_POLICY_KEYS,
        release,
        revision,
        await checks.resolve_extra_args(license.INPUT_EXTRA_ARGS, release),
        file=path,
    )
    rat_ck = await checks.resolve_cache_key(
        resolve(sql.TaskType.RAT_CHECK),
        rat.CHECK_VERSION,
        rat.INPUT_POLICY_KEYS,
        release,
        revision,
        await checks.resolve_extra_args(rat.INPUT_EXTRA_ARGS, release),
        file=path,
    )
    targz_s_ck = await checks.resolve_cache_key(
        resolve(sql.TaskType.TARGZ_STRUCTURE),
        targz.CHECK_VERSION_STRUCTURE,
        targz.INPUT_POLICY_KEYS,
        release,
        revision,
        await checks.resolve_extra_args(targz.INPUT_EXTRA_ARGS, release),
        file=path,
    )
    tasks = [
        queued(asf_uid, sql.TaskType.COMPARE_SOURCE_TREES, release, revision, path, check_cache_key=compare_ck),
        queued(
            asf_uid,
            sql.TaskType.LICENSE_FILES,
            release,
            revision,
            path,
            check_cache_key=license_f_ck,
            extra_args={"is_podling": is_podling},
        ),
        queued(asf_uid, sql.TaskType.LICENSE_HEADERS, release, revision, path, check_cache_key=license_h_ck),
        queued(asf_uid, sql.TaskType.RAT_CHECK, release, revision, path, check_cache_key=rat_ck),
        queued(asf_uid, sql.TaskType.TARGZ_STRUCTURE, release, revision, path, check_cache_key=targz_s_ck),
    ]

    return await asyncio.gather(*tasks)


async def workflow_update(
    asf_uid: str,
    caller_data: db.Session | None = None,
    schedule: datetime.datetime | None = None,
    schedule_next: bool = False,
) -> sql.Task:
    """Queue a workflow status update task."""
    task_args = args.WorkflowStatusCheck(next_schedule_seconds=0, run_id=0, asf_uid=asf_uid)
    if schedule_next:
        task_args.next_schedule_seconds = _EVERY_2_MINUTES
    async with db.ensure_session(caller_data) as data:
        task = sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.WORKFLOW_STATUS,
            task_args=task_args.model_dump(),
            asf_uid=asf_uid,
            revision_number=None,
            primary_rel_path=None,
            project_key=None,
            version_key=None,
        )
        if schedule:
            task.scheduled = schedule
            await data.begin_immediate()
            await _clear_existing_scheduled(data, sql.TaskType.WORKFLOW_STATUS, asf_uid)
        data.add(task)
        await data.commit()
        await data.flush()
        return task


async def zip_checks(
    asf_uid: str, release: sql.Release, revision: safe.RevisionNumber, path: str, data: db.Session
) -> list[sql.Task | None]:
    """Create check tasks for a .zip file."""
    # This release has committee, as guaranteed in draft_checks
    is_podling = (release.project.committee is not None) and release.project.committee.is_podling
    compare_ck = await checks.resolve_cache_key(
        resolve(sql.TaskType.COMPARE_SOURCE_TREES),
        compare.CHECK_VERSION,
        compare.INPUT_POLICY_KEYS,
        release,
        revision,
        await checks.resolve_extra_args(compare.INPUT_EXTRA_ARGS, release),
        file=path,
    )
    license_h_ck = await checks.resolve_cache_key(
        resolve(sql.TaskType.LICENSE_HEADERS),
        license.CHECK_VERSION_HEADERS,
        license.INPUT_POLICY_KEYS,
        release,
        revision,
        await checks.resolve_extra_args(license.INPUT_EXTRA_ARGS, release),
        file=path,
    )
    license_f_ck = await checks.resolve_cache_key(
        resolve(sql.TaskType.LICENSE_FILES),
        license.CHECK_VERSION_FILES,
        license.INPUT_POLICY_KEYS,
        release,
        revision,
        await checks.resolve_extra_args(license.INPUT_EXTRA_ARGS, release),
        file=path,
    )
    rat_ck = await checks.resolve_cache_key(
        resolve(sql.TaskType.RAT_CHECK),
        rat.CHECK_VERSION,
        rat.INPUT_POLICY_KEYS,
        release,
        revision,
        await checks.resolve_extra_args(rat.INPUT_EXTRA_ARGS, release),
        file=path,
    )
    zip_s_ck = await checks.resolve_cache_key(
        resolve(sql.TaskType.ZIPFORMAT_STRUCTURE),
        zipformat.CHECK_VERSION_STRUCTURE,
        zipformat.INPUT_POLICY_KEYS,
        release,
        revision,
        await checks.resolve_extra_args(zipformat.INPUT_EXTRA_ARGS, release),
        file=path,
    )

    tasks = [
        queued(asf_uid, sql.TaskType.COMPARE_SOURCE_TREES, release, revision, path, check_cache_key=compare_ck),
        queued(
            asf_uid,
            sql.TaskType.LICENSE_FILES,
            release,
            revision,
            path,
            check_cache_key=license_f_ck,
            extra_args={"is_podling": is_podling},
        ),
        queued(asf_uid, sql.TaskType.LICENSE_HEADERS, release, revision, path, check_cache_key=license_h_ck),
        queued(asf_uid, sql.TaskType.RAT_CHECK, release, revision, path, check_cache_key=rat_ck),
        queued(asf_uid, sql.TaskType.ZIPFORMAT_STRUCTURE, release, revision, path, check_cache_key=zip_s_ck),
    ]
    return await asyncio.gather(*tasks)


async def _add_task(data: db.Session, task: sql.Task) -> None:
    try:
        async with data.begin_nested():
            data.add(task)
            await data.flush()
    except sqlalchemy.exc.IntegrityError as e:
        if (
            isinstance(e.orig, sqlite3.IntegrityError)
            and (e.orig.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE)
            and ("task.inputs_hash" in str(e.orig))
        ):
            return
        raise


async def _clear_existing_scheduled(
    data: db.Session,
    task_type: sql.TaskType,
    asf_uid: str,
    *,
    include_unscheduled: bool = False,
) -> None:
    via = sql.validate_instrumented_attribute
    conditions: list[Any] = [
        (via(sql.Task.task_type) == task_type),
        (via(sql.Task.asf_uid) == asf_uid),
        (via(sql.Task.status) == sql.TaskStatus.QUEUED),
    ]
    if not include_unscheduled:
        conditions.append(via(sql.Task.scheduled).is_not(None))

    await data.execute(
        sqlmodel.delete(sql.Task).where(
            *conditions,
        )
    )


async def _draft_file_checks(
    asf_uid: str,
    caller_data: db.Session | None,
    data: db.Session,
    path: safe.RelPath,
    previous_version: sql.Release | None,
    project_key: safe.ProjectKey,
    release: sql.Release,
    release_version: safe.VersionKey,
    revision_number: safe.RevisionNumber,
):
    path_str = str(path)
    task_function: Callable[[str, sql.Release, str, str, db.Session], Awaitable[list[sql.Task | None]]] | None = None
    for suffix, func in TASK_FUNCTIONS.items():
        if path.as_path().name.endswith(suffix):
            task_function = func
            break
    if task_function:
        for task in await task_function(asf_uid, release, revision_number, path_str, data):
            if task:
                task.revision_number = str(revision_number)
                await _add_task(data, task)
    # TODO: Should we check .json files for their content?
    # Ideally we would not have to do that
    if path.as_path().name.endswith(".cdx.json"):
        cdx_task = await queued(
            asf_uid,
            sql.TaskType.SBOM_TOOL_SCORE,
            release,
            revision_number,
            path_str,
            extra_args={
                "project_key": str(project_key),
                "version_key": str(release_version),
                "revision_number": str(revision_number),
                "previous_release_version": previous_version.version if previous_version else None,
                "file_path": path_str,
                "asf_uid": asf_uid,
            },
        )
        if cdx_task:
            await _add_task(data, cdx_task)


TASK_FUNCTIONS: Final[dict[str, Callable[..., Coroutine[Any, Any, list[sql.Task | None]]]]] = {
    ".asc": asc_checks,
    ".sha256": sha_checks,
    ".sha512": sha_checks,
    ".tar.gz": tar_gz_checks,
    ".tgz": tar_gz_checks,
    ".zip": zip_checks,
}
