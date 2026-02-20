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
from collections.abc import Awaitable, Callable
from typing import Any

import atr.models.sql as sql
import atr.tasks.checks as checks


class RecorderStub(checks.Recorder):
    def __init__(self, path: pathlib.Path, checker: str) -> None:
        super().__init__(
            checker=checker,
            inputs_hash=None,
            project_name="test",
            version_name="test",
            revision_number="00001",
            primary_rel_path=None,
            member_rel_path=None,
            afresh=False,
        )
        self._path = path
        self.messages: list[tuple[str, str, dict | None]] = []

    async def abs_path(self, rel_path: str | None = None) -> pathlib.Path | None:
        return self._path if (rel_path is None) else self._path / rel_path

    async def primary_path_is_binary(self) -> bool:
        return False

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
            release_name=self.release_name,
            revision_number=self.revision_number,
            checker=self.checker,
            primary_rel_path=primary_rel_path,
            member_rel_path=member_rel_path,
            created=datetime.datetime.now(datetime.UTC),
            status=status,
            message=message,
            data=data,
            inputs_hash=None,
        )

    async def exception(
        self, message: str, data: Any, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> sql.CheckResult:
        return await self._add(sql.CheckResultStatus.EXCEPTION, message, data, primary_rel_path, member_rel_path)

    async def failure(
        self, message: str, data: Any, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> sql.CheckResult:
        return await self._add(sql.CheckResultStatus.FAILURE, message, data, primary_rel_path, member_rel_path)

    async def success(
        self, message: str, data: Any, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> sql.CheckResult:
        return await self._add(sql.CheckResultStatus.SUCCESS, message, data, primary_rel_path, member_rel_path)

    async def warning(
        self, message: str, data: Any, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> sql.CheckResult:
        return await self._add(sql.CheckResultStatus.WARNING, message, data, primary_rel_path, member_rel_path)


def get_recorder(recorder: checks.Recorder) -> Callable[[], Awaitable[checks.Recorder]]:
    async def _recorder() -> checks.Recorder:
        return recorder

    return _recorder
