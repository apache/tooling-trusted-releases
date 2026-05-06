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

import pathlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

import atr.get.file as file_get
import atr.models.safe as safe
import atr.models.sql as sql
import atr.util as util


class FakeReleaseWithoutRevisions:
    phase = sql.ReleasePhase.RELEASE

    @property
    def safe_latest_revision_number(self) -> safe.RevisionNumber:
        raise ValueError("Release has no revisions")


@pytest.mark.asyncio
async def test_release_file_stats_skips_revision_lookup_for_release_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_phase_dirs: list[str] = []

    async def fake_content_list(
        phase_subdir: safe.StatePath, *_args: Any, **_kwargs: Any
    ) -> AsyncIterator[util.FileStat]:
        seen_phase_dirs.append(phase_subdir.name)
        for stat in ():
            yield stat

    monkeypatch.setattr(file_get.util, "content_list", fake_content_list)
    monkeypatch.setattr(file_get.paths, "get_finished_dir", lambda: safe.StatePath(pathlib.Path("/state/finished")))

    release = FakeReleaseWithoutRevisions()
    stats = await file_get._release_file_stats(release, safe.ProjectKey("p"), safe.VersionKey("1.0"))

    assert stats == []
    assert seen_phase_dirs == ["finished"]
    with pytest.raises(ValueError, match="no revisions"):
        _ = release.safe_latest_revision_number
