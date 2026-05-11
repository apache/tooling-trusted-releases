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

import contextlib
from types import SimpleNamespace

import pytest

import atr.construct as construct
import atr.models.safe as safe


class MockQuery:
    def __init__(self, value: object) -> None:
        self._value = value

    async def demand(self, error: Exception) -> object:
        if self._value is None:
            raise error
        return self._value

    async def get(self) -> object:
        return self._value


class MockDBSession:
    def __init__(self, release: object, revision: object) -> None:
        self._release = release
        self._revision = revision

    def release(self, **kwargs: object) -> MockQuery:
        return MockQuery(self._release)

    def revision(self, **kwargs: object) -> MockQuery:
        return MockQuery(self._revision)


@pytest.mark.asyncio
async def test_announce_release_subject_and_body_uses_podling_downloads_url(monkeypatch) -> None:
    committee = SimpleNamespace(key="myproject", is_podling=True, display_name="Apache MyProject (podling)")
    project = SimpleNamespace(
        short_display_name="Apache MyProject",
        name="MyProject",
        key="myproject",
        bug_database="bug_database",
        download_page="download_page",
        homepage="homepage",
        lifecycle_page="lifecycle_page",
        mailing_lists="mailing_lists",
        repository="repository",
    )
    release = SimpleNamespace(key="myproject-1.0.0", committee=committee, project=project)
    revision = SimpleNamespace(number="1", tag=None)

    monkeypatch.setattr(construct.config, "get", lambda: SimpleNamespace(APP_HOST="downloads.apache.org"))
    monkeypatch.setattr(construct.db, "session", _mock_session_factory(MockDBSession(release, revision)))

    subject, body = await construct.announce_release_subject_and_body(
        "{{PROJECT_NAME}} {{VERSION}}",
        "{{DOWNLOAD_URL}}",
        construct.AnnounceReleaseOptions(
            asfuid="sbp",
            fullname="Some Body",
            project_key=safe.ProjectKey("myproject"),
            version_key=safe.VersionKey("1.0.0"),
            revision_number=safe.RevisionNumber("1"),
            download_path_suffix=safe.RelPath("apache-myproject-1.0.0"),
        ),
    )

    assert subject == "Apache MyProject 1.0.0"
    assert body == "https://downloads.apache.org/downloads/incubator/myproject/apache-myproject-1.0.0/"


@pytest.mark.asyncio
async def test_announce_release_subject_and_body_uses_top_level_downloads_url(monkeypatch) -> None:
    committee = SimpleNamespace(key="myproject", is_podling=False, display_name="Apache MyProject")
    project = SimpleNamespace(
        short_display_name="Apache MyProject",
        name="MyProject",
        key="myproject",
        bug_database="bug_database",
        download_page="download_page",
        homepage="homepage",
        lifecycle_page="lifecycle_page",
        mailing_lists="mailing_lists",
        repository="repository",
    )
    release = SimpleNamespace(key="myproject-1.0.0", committee=committee, project=project)
    revision = SimpleNamespace(number="1", tag=None)

    monkeypatch.setattr(construct.config, "get", lambda: SimpleNamespace(APP_HOST="downloads.apache.org"))
    monkeypatch.setattr(construct.db, "session", _mock_session_factory(MockDBSession(release, revision)))

    _subject, body = await construct.announce_release_subject_and_body(
        "{{PROJECT_NAME}} {{VERSION}}",
        "{{DOWNLOAD_URL}}",
        construct.AnnounceReleaseOptions(
            asfuid="sbp",
            fullname="Some Body",
            project_key=safe.ProjectKey("myproject"),
            version_key=safe.VersionKey("1.0.0"),
            revision_number=safe.RevisionNumber("1"),
        ),
    )

    assert body == "https://downloads.apache.org/downloads/myproject/"


def _mock_session_factory(data: MockDBSession):
    @contextlib.asynccontextmanager
    async def _session():
        yield data

    return _session
