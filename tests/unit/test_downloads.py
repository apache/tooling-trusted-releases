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

import atr.config as config
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.tasks.downloads as downloads
import atr.tasks.task as task
import atr.util as util

PUBLIC_URL = "https://downloads.apache.org/example/1.0"


class Probe:
    def __init__(self, missing: set[str]) -> None:
        self.calls: list[list[str]] = []
        self.missing = missing

    async def __call__(self, target: util.SvnPublishTarget, url: str, paths: list[str]) -> util.PropagationSummary:
        self.calls.append(paths)
        outcomes = [
            util.PropagationOutcome(
                rel_path=path,
                public_url=f"{url}/{path}",
                ok=path not in self.missing,
                status=404 if (path in self.missing) else 200,
                error="unavailable" if (path in self.missing) else None,
            )
            for path in paths
        ]
        return util.PropagationSummary(target, len(paths), sum(outcome.ok for outcome in outcomes), outcomes)


async def test_empty_download_set_is_not_available(monkeypatch) -> None:
    probe = mock.AsyncMock()
    monkeypatch.setattr(util, "check_propagation", probe)
    progress = await downloads._check_paths(PUBLIC_URL, [], None)
    assert not progress.available
    probe.assert_not_awaited()


async def test_every_artifact_is_checked_in_bounded_batches(monkeypatch) -> None:
    paths = [f"{index:03}.tar.gz" for index in range(120)]
    probe = Probe(set())
    monkeypatch.setattr(util, "check_propagation", probe)
    progress = None
    for cursor in [50, 100]:
        with pytest.raises(task.DeferredError) as deferred:
            await downloads._check_paths(PUBLIC_URL, paths, progress)
        progress = deferred.value.result
        assert isinstance(progress, results.DownloadsCheck)
        assert (deferred.value.seconds, progress.cursor, progress.available) == (0, cursor, False)
    progress = await downloads._check_paths(PUBLIC_URL, paths, progress)
    assert progress.available
    assert progress.total == 120
    assert [len(call) for call in probe.calls] == [1, 50, 50, 20]
    assert [path for batch in probe.calls[1:] for path in batch] == paths


@pytest.mark.parametrize("area", ["atr", "release"])
async def test_monitor_checks_downloads_regardless_of_svn_target(monkeypatch, tmp_path, area) -> None:
    monkeypatch.setattr(config, "svn_publish_kind", lambda: config.SvnPublishKind.ASF_DISTRIBUTION)
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", f"https://dist.apache.org/repos/dist/{area}")
    (tmp_path / "a.tar.gz").write_bytes(b"artifact")
    monkeypatch.setattr(downloads.paths, "release_directory", lambda release: safe.StatePath(tmp_path))
    release = types.SimpleNamespace(committee=sql.Committee(key="example"), project=sql.Project(key="example"))
    publication = types.SimpleNamespace(
        task_args={
            "asf_uid": "alice",
            "project_key": "example",
            "version_key": "1.0",
            "revision_number": "00001",
            "download_path_suffix": "1.0",
        }
    )
    data = mock.MagicMock()
    data.__aenter__.return_value = data
    data.task.return_value.get = mock.AsyncMock(return_value=publication)
    data.task.return_value.demand = mock.AsyncMock(return_value=types.SimpleNamespace(result=None))
    data.release.return_value.get = mock.AsyncMock(return_value=release)
    monkeypatch.setattr(downloads.db, "session", lambda: data)
    probe = mock.AsyncMock(wraps=Probe(set()).__call__)
    monkeypatch.setattr(util, "check_propagation", probe)

    progress = await downloads.check({"publish_task_id": 42}, task_id=43)

    assert progress.available
    probe.assert_awaited_with(util.SvnPublishTarget.RELEASE, PUBLIC_URL, ["a.tar.gz"])


async def test_waited_url_moves_and_each_recovery_rechecks_the_set(monkeypatch) -> None:
    paths = ["a.tar.gz", "b.tar.gz", "c.tar.gz"]
    probe = Probe(set(paths))
    monkeypatch.setattr(util, "check_propagation", probe)
    progress = None
    for awaited in paths:
        with pytest.raises(task.DeferredError) as deferred:
            await downloads._check_paths(PUBLIC_URL, paths, progress)
        progress = deferred.value.result
        assert isinstance(progress, results.DownloadsCheck)
        assert (deferred.value.seconds, progress.waiting_for, progress.cursor) == (30, awaited, 0)
        with pytest.raises(task.DeferredError):
            await downloads._check_paths(PUBLIC_URL, paths, progress)
        assert probe.calls[-1] == [awaited]
        probe.missing.remove(awaited)
    progress = await downloads._check_paths(PUBLIC_URL, paths, progress)
    assert progress.available
    assert progress.total == 3
    assert probe.calls.count(paths) == 3
