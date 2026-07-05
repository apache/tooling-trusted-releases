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

from __future__ import annotations

import dataclasses
import datetime
import functools
from typing import TYPE_CHECKING, Any, Final

import aiofiles
import aiofiles.os
import sqlmodel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import atr.models.schema as schema

import atr.attestable as attestable
import atr.classify as classify
import atr.db as db
import atr.hashes as hashes
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as file_paths
import atr.util as util


# Pydantic does not like Callable types, so we use a dataclass instead
# It says: "you should define `Callable`, then call `FunctionArguments.model_rebuild()`"
@dataclasses.dataclass
class FunctionArguments:
    recorder: Callable[[str | None], Awaitable[Recorder]]
    asf_uid: str
    project_key: safe.ProjectKey
    version_key: safe.VersionKey
    revision_number: safe.RevisionNumber
    primary_rel_path: safe.RelPath | None
    extra_args: dict[str, Any]


class Recorder:
    checker: str
    checker_version: str | None
    release_key: safe.ReleaseKey
    project_key: safe.ProjectKey
    version_key: safe.VersionKey
    primary_rel_path: safe.RelPath | None
    member_rel_path: str | None
    revision_number: safe.RevisionNumber
    afresh: bool
    __cached: bool
    __input_hash: str | None

    def __init__(
        self,
        checker: str | Callable[..., Any],
        checker_version: str | None,
        inputs_hash: str | None,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        revision_number: safe.RevisionNumber,
        primary_rel_path: safe.RelPath | None = None,
        member_rel_path: str | None = None,
        afresh: bool = True,
    ) -> None:
        self.checker = function_key(checker)
        self.checker_version = checker_version
        self.release_key = sql.release_key(project_key, version_key)
        self.revision_number = revision_number
        self.primary_rel_path = primary_rel_path
        self.member_rel_path = member_rel_path
        self.afresh = afresh
        self.constructed = False
        self.member_problems: dict[sql.CheckResultStatus, int] = {}
        self.__cached = False
        self.__input_hash = inputs_hash

        self.project_key = project_key
        self.version_key = version_key

    @classmethod
    async def create(
        cls,
        checker: str | Callable[..., Any],
        checker_version: str | None,
        inputs_hash: str,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        revision_number: safe.RevisionNumber,
        primary_rel_path: safe.RelPath | None = None,
        member_rel_path: str | None = None,
        afresh: bool = True,
    ) -> Recorder:
        recorder = cls(
            checker,
            checker_version,
            inputs_hash,
            project_key,
            version_key,
            revision_number,
            primary_rel_path,
            member_rel_path,
            afresh,
        )
        if afresh is True:
            # Clear outer path whether it's specified or not
            await recorder.clear(
                primary_rel_path=str(primary_rel_path) if primary_rel_path else None, member_rel_path=member_rel_path
            )
        recorder.constructed = True
        return recorder

    async def _add(
        self,
        status: sql.CheckResultStatus,
        message: str,
        data: Any,
        primary_rel_path: safe.RelPath | None = None,
        member_rel_path: str | None = None,
    ) -> sql.CheckResult:
        if self.constructed is False:
            raise RuntimeError("Cannot add check result to a recorder that has not been constructed")
        if self.checker_version is None:
            raise RuntimeError("checker_version must be set before recording results")
        if primary_rel_path is not None:
            if self.primary_rel_path is not None:
                raise ValueError("Cannot specify path twice")
            # if self.afresh is True:
            #     # Clear inner path only if it's specified
            #     await self.clear(primary_rel_path=primary_rel_path, member_rel_path=member_rel_path)

        if member_rel_path is not None:
            if status != sql.CheckResultStatus.NOTE:
                self.member_problems[status] = self.member_problems.get(status, 0) + 1

        result = sql.CheckResult(
            release_key=str(self.release_key),
            revision_number=str(self.revision_number),
            checker=self.checker,
            checker_version=self.checker_version,
            primary_rel_path=str(primary_rel_path or self.primary_rel_path)
            if (primary_rel_path or self.primary_rel_path)
            else None,
            member_rel_path=member_rel_path,
            created=datetime.datetime.now(datetime.UTC),
            status=status,
            message=message,
            data=data,
            cached=False,
            inputs_hash=self.input_hash,
        )

        # It would be more efficient to keep a session open
        # But, we prefer in this case to maintain a simpler interface
        # If performance is unacceptable, we can revisit this design
        async with db.session() as session:
            session.add(result)
            await session.commit()
        return result

    async def abs_path(self, rel_path: safe.RelPath | str | None = None) -> safe.StatePath | None:
        """Construct the absolute path using the required revision."""
        # Determine the relative path part
        rel_path_part: safe.RelPath | str | None = None
        if rel_path is not None:
            rel_path_part = rel_path
        elif self.primary_rel_path is not None:
            rel_path_part = self.primary_rel_path

        if rel_path_part is None:
            return self.abs_path_base()
        return self.abs_path_base() / rel_path_part

    def abs_path_base(self) -> safe.StatePath:
        return file_paths.base_path_for_revision(self.project_key, self.version_key, self.revision_number)

    async def project(self) -> sql.Project:
        # TODO: Cache project
        async with db.session() as data:
            name_str = str(self.project_key)
            return await data.project(key=name_str, _release_policy=True).demand(
                RuntimeError(f"Project {name_str} not found")
            )

    async def primary_path_is_binary(self) -> bool:
        if self.primary_rel_path is None:
            return False
        return (await self._classify_primary_path()) == classify.FileType.BINARY

    async def primary_path_is_source(self) -> bool:
        if self.primary_rel_path is None:
            return False
        return (await self._classify_primary_path()) == classify.FileType.SOURCE

    async def _classify_primary_path(self) -> classify.FileType:
        if self.primary_rel_path is None:
            return classify.FileType.BINARY
        release_key = str(self.release_key)
        revision_seq = int(str(self.revision_number))
        async with db.session() as data:
            classification = await data.release_file_classification_at(
                release_key, str(self.primary_rel_path), revision_seq
            )
        if classification is not None:
            return classify.FileType(classification)
        project = await self.project()
        base_path = self.abs_path_base()
        # TODO: This should get the matchers from attestable data policy
        # But this branch is only a fallback for pre-AttestableV2 releases
        source_matcher, binary_matcher = classify.matchers_from_policy(
            project.policy_source_artifact_paths,
            project.policy_binary_artifact_paths,
            base_path,
        )
        return await classify.classify(
            self.primary_rel_path,
            base_path=base_path,
            source_matcher=source_matcher,
            binary_matcher=binary_matcher,
        )

    @property
    def cached(self) -> bool:
        return self.__cached

    async def clear(self, primary_rel_path: str | None = None, member_rel_path: str | None = None) -> None:
        async with db.session() as data:
            stmt = sqlmodel.delete(sql.CheckResult).where(
                sql.validate_instrumented_attribute(sql.CheckResult.inputs_hash) == self.input_hash,
                sql.validate_instrumented_attribute(sql.CheckResult.primary_rel_path) == primary_rel_path,
            )
            await data.execute(stmt)
            await data.commit()

    @property
    def input_hash(self) -> str | None:
        return self.__input_hash

    async def blocker(
        self,
        message: str,
        data: Any,
        primary_rel_path: safe.RelPath | None = None,
        member_rel_path: str | None = None,
    ) -> sql.CheckResult:
        result = await self._add(
            sql.CheckResultStatus.BLOCKER,
            message,
            data,
            primary_rel_path=primary_rel_path,
            member_rel_path=member_rel_path,
        )
        return result

    async def concern(
        self,
        message: str,
        data: Any,
        primary_rel_path: safe.RelPath | None = None,
        member_rel_path: str | None = None,
    ) -> sql.CheckResult:
        result = await self._add(
            sql.CheckResultStatus.CONCERN,
            message,
            data,
            primary_rel_path=primary_rel_path,
            member_rel_path=member_rel_path,
        )
        return result

    async def exception(
        self,
        message: str,
        data: Any,
        primary_rel_path: safe.RelPath | None = None,
        member_rel_path: str | None = None,
    ) -> sql.CheckResult:
        result = await self._add(
            sql.CheckResultStatus.EXCEPTION,
            message,
            data,
            primary_rel_path=primary_rel_path,
            member_rel_path=member_rel_path,
        )
        return result

    async def note(
        self,
        message: str,
        data: Any,
        primary_rel_path: safe.RelPath | None = None,
        member_rel_path: str | None = None,
    ) -> sql.CheckResult:
        result = await self._add(
            sql.CheckResultStatus.NOTE,
            message,
            data,
            primary_rel_path=primary_rel_path,
            member_rel_path=member_rel_path,
        )
        return result

    async def suggestion(
        self,
        message: str,
        data: Any,
        primary_rel_path: safe.RelPath | None = None,
        member_rel_path: str | None = None,
    ) -> sql.CheckResult:
        result = await self._add(
            sql.CheckResultStatus.SUGGESTION,
            message,
            data,
            primary_rel_path=primary_rel_path,
            member_rel_path=member_rel_path,
        )
        return result


def function_key(func: Callable[..., Any] | str) -> str:
    return func.__module__ + "." + func.__name__ if callable(func) else func


async def resolve_archive_dir(args: FunctionArguments) -> safe.StatePath | None:
    """Resolve the extracted archive directory for the primary archive."""
    if args.primary_rel_path is None:
        return None
    release_key = sql.release_key(str(args.project_key), str(args.version_key))
    revision_seq = int(str(args.revision_number))
    async with db.session() as data:
        content_hash = await data.release_file_hash_at(release_key, str(args.primary_rel_path), revision_seq)
    if content_hash is None:
        abs_path = file_paths.revision_path_for_file(
            args.project_key, args.version_key, args.revision_number, str(args.primary_rel_path)
        )
        if await aiofiles.os.path.isfile(abs_path):
            content_hash = await hashes.compute_file_hash(abs_path)
    if content_hash is None:
        return None
    archive_key = hashes.filesystem_archives_key(content_hash)
    archive_dir = file_paths.get_archives_dir() / str(args.project_key) / str(args.version_key) / archive_key
    if await aiofiles.os.path.isdir(archive_dir):
        return archive_dir
    return None


async def resolve_cache_key(  # noqa: C901
    checker: str | Callable[..., Any],
    checker_version: str,
    policy_keys: list[str],
    release: sql.Release,
    revision: safe.RevisionNumber,
    args: dict[str, Any] | None = None,
    file: str | None = None,
    path: safe.StatePath | None = None,
    ignore_path: bool = False,
) -> dict[str, Any] | None:
    if not args:
        args = {}
    cache_key = {"checker": function_key(checker), "version": checker_version}
    file_hash = None
    attestable_data = await attestable.load(release.safe_project_key, release.safe_version_key, revision)
    if attestable_data:
        policy_dict = _coerce_policy_nulls(attestable_data.policy)
        policy = sql.ReleasePolicy.model_validate(policy_dict)
        if not ignore_path:
            file_hash = attestable.path_hash(attestable_data, file) if file else None
    else:
        # TODO: Is this fallback valid / necessary? Or should we bail out if there's no attestable data?
        policy = release.release_policy or release.project.release_policy
    if not ignore_path:
        if file:
            release_key = sql.release_key(str(release.safe_project_key), str(release.safe_version_key))
            revision_seq = int(str(revision))
            async with db.session() as data:
                file_hash = await data.release_file_hash_at(release_key, file, revision_seq)
        if file_hash is None:
            if path is None:
                path = file_paths.revision_path_for_file(
                    release.safe_project_key, release.safe_version_key, revision, file or ""
                )
            file_hash = await hashes.compute_file_hash(path)
    if file_hash:
        cache_key["file_hash"] = file_hash
    if release.check_cache_key:
        cache_key["release_cache_key"] = release.check_cache_key

    if (len(policy_keys) > 0) and (policy is not None):
        policy_dict = policy.model_dump(exclude_none=True)
        return {**cache_key, **args, **{k: policy_dict[k] for k in policy_keys if k in policy_dict}}
    else:
        return {**cache_key, **args}


async def resolve_extra_args(arg_names: list[str], release: sql.Release, rel_path: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in arg_names:
        resolver = _EXTRA_ARG_RESOLVERS.get(name, None)
        # If we can't find a resolver, we'll carry on anyway since it'll just mean no cache potentially
        if resolver is None:
            log.warning(f"Unknown extra arg resolver: {name}")
            return {}
        result[name] = await resolver(release, rel_path)
    return result


def with_model(cls: type[schema.Strict]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to specify the parameters for a check."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(data_dict: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            model_instance = cls(**data_dict)
            return await func(model_instance, *args, **kwargs)

        return wrapper

    return decorator


def _coerce_policy_nulls(policy: dict[str, Any]) -> dict[str, Any]:
    # This is a shim for a bug in the models persisted in attestable data
    # The bug is linted by scripts/check_nullable_fields.py
    result = dict(policy)
    for key, value in result.items():
        if (value is not None) or (key not in sql.ReleasePolicy.model_fields):
            continue
        field = sql.ReleasePolicy.model_fields[key]
        if (field.default_factory is not None) and (field.default_factory is list):
            result[key] = []
    return result


async def _resolve_all_files(release: sql.Release, rel_path: str | None = None) -> list[str]:
    if not release.latest_revision_number:
        return []
    if not (
        base_path := file_paths.base_path_for_revision(
            release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
        )
    ):
        return []

    if not await aiofiles.os.path.isdir(base_path):
        log.error(f"Base release directory does not exist or is not a directory: {base_path}")
        return []
    relative_paths = [p async for p in util.paths_recursive(base_path)]
    relative_paths_set = set(str(p) for p in relative_paths)
    return list(sorted(relative_paths_set))


async def _resolve_committee_key(release: sql.Release, rel_path: str | None = None) -> str:
    if release.committee is None:
        raise ValueError("Release has no committee")
    return release.committee.key


async def _resolve_committee_signing_keys(release: sql.Release, rel_path: str | None = None) -> list[str]:
    if release.committee is None:
        raise ValueError("Release has no committee")
    via = sql.validate_instrumented_attribute
    committee_key = release.committee.key
    async with db.session() as data:
        statement = (
            sqlmodel.select(via(sql.KeyLink.key_fingerprint))
            .join(sql.PublicSigningKey)
            .where(
                via(sql.KeyLink.committee_key) == committee_key,
                via(sql.PublicSigningKey.deleted).is_(None),
            )
        )
        result = await data.execute(statement)
        fingerprints = result.scalars().all()
    return sorted(fp for fp in fingerprints if fp)


async def _resolve_cross_format_sibling_swhids(release: sql.Release, rel_path: str | None = None) -> list[str]:
    if (not rel_path) or (not release.latest_revision_number):
        return []
    attestable_data = await attestable.load(
        release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
    )
    if attestable_data is None:
        return []
    siblings = attestable.cross_format_siblings(attestable_data, rel_path)
    return sorted(f"{path}={swhid or ''}" for path, swhid in siblings.items())


async def _resolve_github_tp_sha(release: sql.Release, rel_path: str | None = None) -> str:
    if not release.latest_revision_number:
        return ""
    payload = await attestable.github_tp_payload_read(
        release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
    )
    if not payload:
        return ""
    return payload.sha


async def _resolve_is_podling(release: sql.Release, rel_path: str | None = None) -> bool:
    return (release.committee is not None) and release.committee.is_podling


async def _resolve_unsuffixed_file_hash(release: sql.Release, rel_path: str | None = None) -> str:
    if (not rel_path) or (not release.latest_revision_number):
        return ""
    abs_path = file_paths.revision_path_for_file(
        release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number, rel_path
    )
    plain_path = abs_path.path.with_suffix("")
    if await aiofiles.os.path.isfile(plain_path):
        return await hashes.compute_file_hash(plain_path)
    else:
        return ""


_EXTRA_ARG_RESOLVERS: Final[dict[str, Callable[[sql.Release, str | None], Any]]] = {
    "all_files": _resolve_all_files,
    "committee_key": _resolve_committee_key,
    "committee_signing_keys": _resolve_committee_signing_keys,
    "cross_format_sibling_swhids": _resolve_cross_format_sibling_swhids,
    "github_tp_sha": _resolve_github_tp_sha,
    "is_podling": _resolve_is_podling,
    "unsuffixed_file_hash": _resolve_unsuffixed_file_hash,
}
