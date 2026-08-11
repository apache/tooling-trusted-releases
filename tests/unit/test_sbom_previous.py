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
import unittest.mock as mock

import pytest

import atr.config as config
import atr.models.args as args
import atr.models.safe as safe
import atr.tasks.sbom as sbom_tasks

_BOM_TEXT = '{"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1}'


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status: int, chunks: list[bytes]) -> None:
        self.status = status
        self.content_length = sum(len(chunk) for chunk in chunks)
        self.content = FakeContent(chunks)

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.get_kwargs: dict[str, object] | None = None

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.get_kwargs = kwargs
        return self._response


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.public: bool | None = None

    def __call__(self, timeout: object = None, public: bool = False) -> FakeSession:
        self.public = public
        return self.session


def make_score_args(previous: str | None) -> args.ScoreArgs:
    return args.ScoreArgs(
        project_key=safe.ProjectKey("proj"),
        version_key=safe.VersionKey("1.0"),
        revision_number=safe.RevisionNumber("00001"),
        file_path=safe.RelPath("x.cdx.json"),
        previous_release_version=safe.VersionKey(previous) if previous else None,
    )


async def test_fetch_previous_sbom_refuses_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = FakeSessionFactory(FakeSession(FakeResponse(302, [b"ignored"])))
    monkeypatch.setattr(sbom_tasks.util, "create_secure_session", factory)

    assert await sbom_tasks._fetch_previous_sbom("https://downloads.apache.org/p/x.cdx.json") is None


async def test_fetch_previous_sbom_returns_body(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = FakeSessionFactory(FakeSession(FakeResponse(200, [b'{"a"', b": 1}"])))
    monkeypatch.setattr(sbom_tasks.util, "create_secure_session", factory)

    text = await sbom_tasks._fetch_previous_sbom("https://downloads.apache.org/p/x.cdx.json")

    assert text == '{"a": 1}'
    assert factory.public is True
    assert factory.session.get_kwargs == {"allow_redirects": False}


async def test_previous_bundle_fetches_published(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "FINISHED_STORAGE_DIR", str(tmp_path / "missing"), raising=False)
    monkeypatch.setattr(
        sbom_tasks, "_previous_sbom_url", mock.AsyncMock(return_value="https://downloads.apache.org/p/x.cdx.json")
    )
    monkeypatch.setattr(sbom_tasks, "_fetch_previous_sbom", mock.AsyncMock(return_value=_BOM_TEXT))

    bundle = await sbom_tasks._previous_bundle(make_score_args("0.9"), "x.cdx.json")

    assert bundle is not None
    assert bundle.bom.version == 1


async def test_previous_bundle_prefers_local(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "FINISHED_STORAGE_DIR", str(tmp_path), raising=False)
    write_finished_sbom(tmp_path)
    monkeypatch.setattr(sbom_tasks, "_previous_sbom_url", mock.AsyncMock(side_effect=AssertionError))

    bundle = await sbom_tasks._previous_bundle(make_score_args("0.9"), "x.cdx.json")

    assert bundle is not None
    assert bundle.bom.version == 1


async def test_previous_bundle_skips_unrecorded(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "FINISHED_STORAGE_DIR", str(tmp_path / "missing"), raising=False)
    monkeypatch.setattr(sbom_tasks, "_previous_sbom_url", mock.AsyncMock(return_value=None))

    assert await sbom_tasks._previous_bundle(make_score_args("0.9"), "x.cdx.json") is None


async def test_previous_bundle_without_previous_version() -> None:
    assert await sbom_tasks._previous_bundle(make_score_args(None), "x.cdx.json") is None


def write_finished_sbom(tmp_path: pathlib.Path) -> None:
    directory = tmp_path / "proj" / "0.9"
    directory.mkdir(parents=True)
    (directory / "x.cdx.json").write_text(_BOM_TEXT, encoding="utf-8")
