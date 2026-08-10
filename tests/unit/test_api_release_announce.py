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
import werkzeug.exceptions as exceptions

import atr.api as api
import atr.construct as construct
import atr.db as db
import atr.models as models
import atr.models.safe as safe
import atr.storage as storage


class MockQuery:
    def __init__(self, value: object) -> None:
        self._value = value

    async def demand(self, error: Exception) -> object:
        if self._value is None:
            raise error
        return self._value


class MockDBSession:
    def __init__(self, release: object) -> None:
        self._release = release

    def release(self, **kwargs: object) -> MockQuery:
        return MockQuery(self._release)


@pytest.mark.asyncio
async def test_release_announce_explicit_revision_is_rendered(monkeypatch) -> None:
    calls = _patch_rendering(monkeypatch)

    body, fullname = await api._release_announce_body(_args(revision=safe.RevisionNumber("00002")), "user")

    assert (body, fullname) == ("RENDERED BODY", "Example User")
    assert calls["options"].revision_number == safe.RevisionNumber("00002")


@pytest.mark.asyncio
async def test_release_announce_omitted_body_renders_policy_template(monkeypatch) -> None:
    calls = _patch_rendering(monkeypatch)

    body, fullname = await api._release_announce_body(_args(), "user")

    assert (body, fullname) == ("RENDERED BODY", "Example User")
    assert calls["subject"] == ""
    assert calls["body"] == "Body {{VERSION}}"
    assert calls["options"].revision_number == safe.RevisionNumber("00003")
    assert calls["options"].fullname == "Example User"


@pytest.mark.asyncio
async def test_release_announce_supplied_body_passes_through(monkeypatch) -> None:
    calls = _patch_rendering(monkeypatch)

    body, fullname = await api._release_announce_body(_args(body="Custom body"), "user")

    assert (body, fullname) == ("Custom body", "Example User")
    assert calls == {}


def test_release_announce_unreachable_translates_to_service_unavailable() -> None:
    error = storage.PropagationUnreachableError("The download server could not be checked", status=503)

    exc = api._http_exception_from_storage_access_error(error)

    assert isinstance(exc, exceptions.ServiceUnavailable)


def _args(body: str | None = None, revision: safe.RevisionNumber | None = None) -> models.api.ReleaseAnnounceArgs:
    return models.api.ReleaseAnnounceArgs(
        project=safe.ProjectKey("example"),
        version=safe.VersionKey("1.0.0"),
        revision=revision,
        email_to="announce@example.apache.org",
        body=body,
        path_suffix=safe.RelPath("example/1.0.0"),
    )


def _patch_rendering(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {}

    async def fake_render(subject: str, body: str, options: construct.AnnounceReleaseOptions) -> tuple[str, str]:
        calls["subject"] = subject
        calls["body"] = body
        calls["options"] = options
        return "RENDERED SUBJECT", "RENDERED BODY"

    async def fake_fullname(asf_uid: str) -> str:
        return "Example User"

    release = SimpleNamespace(
        safe_latest_revision_number=safe.RevisionNumber("00003"),
        project=SimpleNamespace(policy_announce_release_template="Body {{VERSION}}"),
    )

    @contextlib.asynccontextmanager
    async def fake_session():
        yield MockDBSession(release)

    monkeypatch.setattr(api, "_ldap_fullname", fake_fullname)
    monkeypatch.setattr(construct, "announce_release_subject_and_body", fake_render)
    monkeypatch.setattr(db, "session", fake_session)
    return calls
