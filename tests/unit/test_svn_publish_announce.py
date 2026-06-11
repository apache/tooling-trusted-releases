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

import pytest

import atr.constants as constants
import atr.models.safe as safe
import atr.storage.writers.announce as announce_writer
import atr.util as util


async def fake_check_propagation(
    target: util.SvnPublishTarget,
    public_url: str,
    rel_paths: list[str],
) -> util.PropagationSummary:
    return util.PropagationSummary(
        target=target,
        total=1,
        reachable=0,
        outcomes=[
            util.PropagationOutcome(
                rel_path=rel_paths[0],
                public_url=f"{public_url}/{rel_paths[0]}",
                ok=False,
                status=404,
                error="HTTP 404",
            )
        ],
    )


async def test_announce_publication_artifact_check_warns_for_unreachable_artifact(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_text("content", encoding="utf-8")

    monkeypatch.setattr(announce_writer.util, "check_propagation", fake_check_propagation)
    warnings: list[str] = []
    monkeypatch.setattr(announce_writer.log, "warning", warnings.append)
    writer = object.__new__(announce_writer.ReleaseManager)
    check = getattr(writer, "_ReleaseManager__warn_publication_artifacts")

    await check(safe.StatePath(tmp_path), util.SvnPublishTarget.RELEASE, f"{constants.DOWNLOADS_APACHE_URL}/project")

    assert any("artifact.tar.gz" in warning for warning in warnings)
