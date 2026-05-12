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
from collections.abc import Awaitable, Callable
from typing import Any

import atr.models.safe as safe
import atr.models.sql as sql
import atr.tasks.checks as checks


class RecorderStub(checks.Recorder):
    def __init__(self, path: safe.StatePath, checker: str, checker_version: str | None = None) -> None:
        super().__init__(
            checker=checker,
            checker_version=checker_version,
            inputs_hash=None,
            project_key=safe.ProjectKey("test"),
            version_key=safe.VersionKey("test"),
            revision_number=safe.RevisionNumber("00001"),
            primary_rel_path=None,
            member_rel_path=None,
            afresh=False,
        )
        self._path = path
        self.messages: list[tuple[str, str, dict | None]] = []

    async def abs_path(self, rel_path: str | None = None) -> safe.StatePath | None:
        return self._path if (rel_path is None) else self._path / rel_path

    async def primary_path_is_source(self) -> bool:
        return True

    async def _add(
        self,
        status: sql.CheckResultStatus,
        message: str,
        data: object,
        primary_rel_path: str | None = None,
        member_rel_path: str | None = None,
    ) -> sql.CheckResult:
        self.messages.append((status.value, message, data if isinstance(data, dict) else None))
        return sql.CheckResult(
            id=0,
            release_key=self.release_key,
            revision_number=self.revision_number,
            checker=self.checker,
            checker_version=self.checker_version,
            primary_rel_path=primary_rel_path,
            member_rel_path=member_rel_path,
            created=datetime.datetime.now(datetime.UTC),
            status=status,
            message=message,
            data=data,
            inputs_hash=None,
        )

    async def concern(
        self, message: str, data: Any, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> sql.CheckResult:
        return await self._add(sql.CheckResultStatus.CONCERN, message, data, primary_rel_path, member_rel_path)

    async def exception(
        self, message: str, data: Any, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> sql.CheckResult:
        return await self._add(sql.CheckResultStatus.EXCEPTION, message, data, primary_rel_path, member_rel_path)

    async def note(
        self, message: str, data: Any, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> sql.CheckResult:
        return await self._add(sql.CheckResultStatus.NOTE, message, data, primary_rel_path, member_rel_path)

    async def suggestion(
        self, message: str, data: Any, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> sql.CheckResult:
        return await self._add(sql.CheckResultStatus.SUGGESTION, message, data, primary_rel_path, member_rel_path)


def get_recorder(recorder: checks.Recorder) -> Callable[[str | None], Awaitable[checks.Recorder]]:
    async def _recorder(_version: str | None) -> checks.Recorder:
        return recorder

    return _recorder
