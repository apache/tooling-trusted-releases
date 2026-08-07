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

import types
import unittest.mock as mock

import pytest

import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe


class CheckResultQuery:
    def __init__(self, results: list[object]) -> None:
        self._results = results

    async def all(self) -> list[object]:
        return self._results

    def order_by(self, *args: object, **kwargs: object) -> "CheckResultQuery":
        return self


class ReleaseQuery:
    async def get(self) -> object:
        return types.SimpleNamespace(key="proj-1.0")


class CheckResultSession:
    def __init__(self, results: list[object]) -> None:
        self.kwargs: dict[str, object] | None = None
        self._results = results

    async def __aenter__(self) -> "CheckResultSession":
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> bool:
        return False

    def check_result(self, **kwargs: object) -> CheckResultQuery:
        self.kwargs = kwargs
        return CheckResultQuery(self._results)

    def release(self, **_kwargs: object) -> ReleaseQuery:
        return ReleaseQuery()


@pytest.mark.asyncio
async def test_check_results_for_revision_uses_attestable_check_hash() -> None:
    check_result = types.SimpleNamespace(id=1)
    session = CheckResultSession([check_result])
    checks_data = {
        "artifact.tar.gz.asc": {
            "atr.tasks.checks.signature.check": "blake3:signature",
            "other.checker": "blake3:other",
        }
    }

    with (
        mock.patch.object(interaction.attestable, "load_checks", new=mock.AsyncMock(return_value=checks_data)),
        mock.patch.object(db, "ensure_session", return_value=session),
    ):
        results = await interaction.check_results_for_revision(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            safe.RevisionNumber("00005"),
            checker="atr.tasks.checks.signature.check",
            rel_path="artifact.tar.gz.asc",
        )

    assert results == [check_result]
    assert session.kwargs == {
        "checker": "atr.tasks.checks.signature.check",
        "inputs_hash_in": ["blake3:signature"],
        "primary_rel_path": "artifact.tar.gz.asc",
    }


@pytest.mark.asyncio
async def test_check_results_for_revision_uses_legacy_revision_fallback() -> None:
    check_result = types.SimpleNamespace(id=1)
    session = CheckResultSession([check_result])

    with (
        mock.patch.object(interaction.attestable, "load_checks", new=mock.AsyncMock(return_value={})),
        mock.patch.object(db, "ensure_session", return_value=session),
    ):
        results = await interaction.check_results_for_revision(
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            safe.RevisionNumber("00005"),
            checker="atr.tasks.checks.signature.check",
            include_legacy_revision_results=True,
            rel_path="artifact.tar.gz.asc",
        )

    assert results == [check_result]
    assert session.kwargs == {
        "checker": "atr.tasks.checks.signature.check",
        "primary_rel_path": "artifact.tar.gz.asc",
        "release_key": "proj-1.0",
        "revision_number": "00005",
    }
