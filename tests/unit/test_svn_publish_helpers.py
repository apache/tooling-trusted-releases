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

import pytest

import atr.config as config
import atr.constants as constants
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.util as util


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class FakeSession:
    def __init__(self, head_status: int) -> None:
        self.head_status = head_status
        self.head_urls: list[str] = []

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def head(self, url: str, **kwargs: object) -> FakeResponse:
        self.head_urls.append(url)
        return FakeResponse(self.head_status)


class HangingResponse:
    async def __aenter__(self) -> "HangingResponse":
        await asyncio.sleep(60)
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class MixedSession:
    async def __aenter__(self) -> "MixedSession":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def head(self, url: str, **kwargs: object) -> FakeResponse | HangingResponse:
        if "hang" in url:
            return HangingResponse()
        return FakeResponse(404)


async def test_check_propagation_caps_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession(200)
    monkeypatch.setattr(util, "create_secure_session", lambda timeout=None: session)

    summary = await util.check_propagation(
        util.SvnPublishTarget.RELEASE,
        f"{constants.DOWNLOADS_APACHE_URL}/project",
        [f"artifact-{i}.tar.gz" for i in range(util.MAX_PROPAGATION_ARTIFACTS + 1)],
    )

    assert summary.total == util.MAX_PROPAGATION_ARTIFACTS
    assert summary.unprobed == 1
    assert summary.reachable == util.MAX_PROPAGATION_ARTIFACTS


async def test_check_propagation_preserves_results_past_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(util, "create_secure_session", lambda timeout=None: MixedSession())
    monkeypatch.setattr(util, "PROPAGATION_PROBE_DEADLINE", 0.05)

    summary = await util.check_propagation(
        util.SvnPublishTarget.RELEASE,
        f"{constants.DOWNLOADS_APACHE_URL}/project",
        ["hang.tar.gz", "miss.tar.gz"],
    )

    assert [outcome.rel_path for outcome in summary.missing] == ["miss.tar.gz"]
    assert [outcome.rel_path for outcome in summary.unreachable] == ["hang.tar.gz"]


async def test_check_propagation_production_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession(200)
    monkeypatch.setattr(util, "create_secure_session", lambda timeout=None: session)

    summary = await util.check_propagation(
        util.SvnPublishTarget.RELEASE,
        f"{constants.DOWNLOADS_APACHE_URL}/project",
        ["artifact.tar.gz"],
    )

    assert summary.reachable == 1
    assert summary.outcomes[0].public_url == f"{constants.DOWNLOADS_APACHE_URL}/project/artifact.tar.gz"


async def test_check_propagation_test_target_url(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession(200)
    monkeypatch.setattr(util, "create_secure_session", lambda timeout=None: session)

    summary = await util.check_propagation(
        util.SvnPublishTarget.ATR,
        f"{config.get().SVN_DIST_PUBLIC_URL}/project",
        ["artifact.tar.gz"],
    )

    assert summary.reachable == 1
    assert summary.outcomes[0].public_url == f"{config.get().SVN_DIST_PUBLIC_URL}/project/artifact.tar.gz"


def test_download_url_for_path_uses_archive_when_archived(monkeypatch: pytest.MonkeyPatch) -> None:
    # The catalogue passes the artifact's stored full path, so a retired project resolves under its
    # original directory on archive.apache.org regardless of the configured live distribution target.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", None)

    url = util.download_url_for_path(
        safe.RelPath("abdera/1.0/apache-abdera-1.0.tar.gz"), util.DownloadFile.ARTIFACT, archived=True
    )

    assert url == f"{constants.ARCHIVE_APACHE_URL}/abdera/1.0/apache-abdera-1.0.tar.gz"


def test_download_url_for_path_uses_canonical_host_for_released_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", "https://dist.apache.org/repos/dist/atr")

    url = util.download_url_for_path(
        safe.RelPath("abdera/1.0/apache-abdera-1.0.tar.gz.asc"), util.DownloadFile.METADATA
    )

    assert url == f"{constants.DOWNLOADS_APACHE_URL}/abdera/1.0/apache-abdera-1.0.tar.gz.asc"


def test_download_url_for_path_uses_mirror_for_released_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", None)
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", "https://dist.apache.org/repos/dist/atr")

    url = util.download_url_for_path(safe.RelPath("abdera/1.0/apache-abdera-1.0.tar.gz"), util.DownloadFile.ARTIFACT)

    assert url == f"{constants.CLOSER_LUA_URL}/abdera/1.0/apache-abdera-1.0.tar.gz?action=download"


def test_publication_check_url_uses_beta_dist_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", "https://dist.apache.org/repos/dist/atr")
    committee = sql.Committee(key="apple", name="Apple", is_podling=False)

    url = util.publication_check_url(
        committee,
        safe.RelPath("apple-1.0"),
        util.DownloadFile.ARTIFACT,
        filename="apple-1.0.tar.gz",
    )

    assert url == "https://dist.apache.org/repos/dist/atr/apple/apple-1.0/apple-1.0.tar.gz"


def test_publication_check_url_uses_canonical_host_for_release_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", "https://dist.apache.org/repos/dist/release")
    committee = sql.Committee(key="apple", name="Apple", is_podling=False)

    url = util.publication_check_url(
        committee, safe.RelPath("apple-1.0"), util.DownloadFile.METADATA, filename="apple-1.0.tar.gz.asc"
    )

    assert url == f"{constants.DOWNLOADS_APACHE_URL}/apple/apple-1.0/apple-1.0.tar.gz.asc"


def test_publication_check_url_uses_mirror_for_release_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", "https://dist.apache.org/repos/dist/release")
    committee = sql.Committee(key="apple", name="Apple", is_podling=False)

    url = util.publication_check_url(
        committee, safe.RelPath("apple-1.0"), util.DownloadFile.ARTIFACT, filename="apple-1.0.tar.gz"
    )

    assert url == f"{constants.CLOSER_LUA_URL}/apple/apple-1.0/apple-1.0.tar.gz?action=download"


def test_publication_check_url_uses_svn_dist_area_for_beta_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", "https://internal.example.invalid/repos/dist/atr")
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", "https://dist.apache.org/repos/dist/atr")
    committee = sql.Committee(key="apple", name="Apple", is_podling=False)

    url = util.publication_check_url(committee, safe.RelPath("apple-1.0"), util.DownloadFile.METADATA)

    assert url == "https://dist.apache.org/repos/dist/atr/apple/apple-1.0"


def test_svn_publish_internal_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", "https://internal.example.invalid/repos/dist/atr")
    committee = sql.Committee(key="apple", name="Apple", is_podling=False)

    target_url = util.svn_publish_internal_url(committee, safe.RelPath("apple-1.0"))

    assert target_url == "https://internal.example.invalid/repos/dist/atr/apple/apple-1.0"


def test_svn_publish_internal_url_rejects_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", None)
    committee = sql.Committee(key="apple", name="Apple", is_podling=False)

    with pytest.raises(ValueError):
        util.svn_publish_internal_url(committee, None)


def test_svn_publish_models_round_trip() -> None:
    task_args = args.SvnPublish(
        asf_uid="alice",
        project_key="apple",
        version_key="1.0",
        revision_number="00001",
    )
    result = results.SvnPublish(
        kind="svn_publish",
        svn_revision=12345,
        message="ok",
    )
    restored = results.ResultsAdapter.validate_python(result.model_dump())

    assert str(task_args.project_key) == "apple"
    assert task_args.download_path_suffix is None
    assert isinstance(restored, results.SvnPublish)
    assert restored.svn_revision == 12345
    assert sql.TaskType.SVN_PUBLISH.label == "SVN publish"


def test_svn_publish_target_from_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", "https://dist.apache.org/repos/dist/release")

    assert util.svn_publish_target() == util.SvnPublishTarget.RELEASE


def test_svn_publish_target_rejects_unknown_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", "https://dist.apache.org/repos/dist/dev")

    with pytest.raises(ValueError):
        util.svn_publish_target()
