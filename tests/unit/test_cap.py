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
import json
import unittest.mock as mock

import pytest
import sqlalchemy.exc

import atr.models.cap
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post.projects as projects
import atr.storage as storage
import atr.storage.writers.project as project
import atr.tasks.cap as cap
import atr.util as util


class Conf:
    CAP_API_BASE_URL = "https://cap.example.test"
    CAP_ROLE_ACCOUNT_TOKEN = "perm-token"


class FakeResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeSession:
    def __init__(self, response: FakeResponse):
        self._response = response

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        return self._response

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        return self._response

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeQuery:
    def __init__(self, row: sql.ApprovalRequest | None):
        self._row = row

    async def get(self, log_query: bool = False) -> sql.ApprovalRequest | None:
        return self._row


class FakeData:
    def __init__(self, row: sql.ApprovalRequest | None):
        self.row = row

    def approval_request(self, **kwargs: object) -> FakeQuery:
        return FakeQuery(self.row)

    async def begin_immediate(self) -> None:
        return None

    def add(self, obj: object) -> None:
        if isinstance(obj, sql.ApprovalRequest) and obj.id is None:
            obj.id = 1
        return None

    async def commit(self) -> None:
        return None

    def expire_all(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class FakeSessionCM:
    def __init__(self, data: FakeData):
        self._data = data

    async def __aenter__(self) -> FakeData:
        return self._data

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeSystemService:
    def __init__(self, record: object):
        self.project_record_approval_outcome = record

    async def __aenter__(self) -> "FakeSystemService":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeUserService:
    def __init__(self) -> None:
        self.notifications_create = mock.AsyncMock()

    async def __aenter__(self) -> "FakeUserService":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeAuthorisation:
    asf_uid = "alice"


class FakeWrite:
    authorisation = FakeAuthorisation()


class FakeWriteAs:
    def append_to_audit_log(self, **kwargs: object) -> None:
        return None


@pytest.fixture
def cap_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(util.config, "get", lambda: Conf)


@pytest.mark.asyncio
async def test_cap_create_question_error(monkeypatch: pytest.MonkeyPatch, cap_config: None) -> None:
    _patch_http(monkeypatch, 500, "boom")
    with pytest.raises(util.FetchError):
        await util.cap_create_question(
            "tok",
            project_id="commons",
            title="t",
            description="d",
            target_audience="a",
            approval_type="majority_approval",
            closes_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
        )


@pytest.mark.asyncio
async def test_cap_create_question_invalid_json(monkeypatch: pytest.MonkeyPatch, cap_config: None) -> None:
    _patch_http(monkeypatch, 201, "<html>not json</html>")
    with pytest.raises(util.FetchError):
        await util.cap_create_question(
            "tok",
            project_id="commons",
            title="t",
            description="d",
            target_audience="a",
            approval_type="majority_approval",
            closes_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
        )


@pytest.mark.asyncio
async def test_cap_create_question_success(monkeypatch: pytest.MonkeyPatch, cap_config: None) -> None:
    _patch_http(monkeypatch, 201, json.dumps({"question_id": 42, "permalink": None}))
    closes_at = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    result = await util.cap_create_question(
        "tok",
        project_id="commons",
        title="t",
        description="d",
        target_audience="a",
        approval_type="majority_approval",
        closes_at=closes_at,
    )
    assert result.question_id == 42


@pytest.mark.asyncio
async def test_cap_mint_token_success(monkeypatch: pytest.MonkeyPatch, cap_config: None) -> None:
    _patch_http(monkeypatch, 201, json.dumps({"token": "temp-token", "scopes": ["ask"]}))
    assert await util.cap_mint_token() == "temp-token"


@pytest.mark.asyncio
async def test_cap_mint_token_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoToken:
        CAP_API_BASE_URL = "https://cap.example.test"
        CAP_ROLE_ACCOUNT_TOKEN = None

    monkeypatch.setattr(util.config, "get", lambda: NoToken)
    with pytest.raises(util.FetchError):
        await util.cap_mint_token()


@pytest.mark.asyncio
async def test_cap_resolve_question_approved(monkeypatch: pytest.MonkeyPatch, cap_config: None) -> None:
    _patch_http(monkeypatch, 200, json.dumps({"outcome": "approved", "permalink": "https://cap/x"}))
    resolution = await util.cap_resolve_question("tok", 42)
    assert resolution is not None
    assert resolution.outcome == "approved"


@pytest.mark.asyncio
async def test_cap_resolve_question_deadline_in_future(monkeypatch: pytest.MonkeyPatch, cap_config: None) -> None:
    _patch_http(monkeypatch, 403, json.dumps({"error": "deadline_in_future"}))
    assert await util.cap_resolve_question("tok", 42) is None


@pytest.mark.asyncio
async def test_cap_resolve_question_error(monkeypatch: pytest.MonkeyPatch, cap_config: None) -> None:
    _patch_http(monkeypatch, 500, "boom")
    with pytest.raises(util.FetchError):
        await util.cap_resolve_question("tok", 42)


@pytest.mark.asyncio
async def test_cap_resolve_question_invalid_json(monkeypatch: pytest.MonkeyPatch, cap_config: None) -> None:
    _patch_http(monkeypatch, 200, "<html>not json</html>")
    with pytest.raises(util.FetchError):
        await util.cap_resolve_question("tok", 42)


@pytest.mark.asyncio
async def test_cap_resolve_question_not_approved(monkeypatch: pytest.MonkeyPatch, cap_config: None) -> None:
    _patch_http(monkeypatch, 200, json.dumps({"outcome": "insufficient_votes"}))
    resolution = await util.cap_resolve_question("tok", 42)
    assert resolution is not None
    assert resolution.outcome == "insufficient_votes"


def test_completion_eligibility_allows_empty_project() -> None:
    requested_at = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    project = _project_with_drafts()
    error = projects._action_eligibility_error(
        project, sql.ApprovalAction.ARCHIVE, 2, completing=True, requested_at=requested_at
    )
    assert error is None


def test_completion_eligibility_allows_prior_drafts() -> None:
    requested_at = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    project = _project_with_drafts(requested_at - datetime.timedelta(days=1))
    error = projects._action_eligibility_error(
        project, sql.ApprovalAction.ARCHIVE, 2, completing=True, requested_at=requested_at
    )
    assert error is None


def test_completion_eligibility_blocks_new_drafts() -> None:
    requested_at = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    project = _project_with_drafts(requested_at + datetime.timedelta(hours=1))
    error = projects._action_eligibility_error(
        project, sql.ApprovalAction.ARCHIVE, 2, completing=True, requested_at=requested_at
    )
    assert error is not None
    assert "created after" in error


@pytest.mark.asyncio
async def test_request_approval_question_id_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    data = FakeData(None)
    data.commit = mock.AsyncMock(
        side_effect=sqlalchemy.exc.IntegrityError(
            "INSERT", {}, Exception("UNIQUE constraint failed: approvalrequest.cap_question_id")
        )
    )
    wacm = project.CommitteeMember(FakeWrite(), FakeWriteAs(), data, "commons")
    monkeypatch.setattr(project.tasks, "cap_approval_resolve", mock.AsyncMock())
    closes_at = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    with pytest.raises(storage.AccessError, match="already recorded"):
        await wacm.request_approval(safe.ProjectKey("commons-foo"), sql.ApprovalAction.ARCHIVE, 42, closes_at)


@pytest.mark.asyncio
async def test_request_approval_schedules_initial_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    data = FakeData(None)
    wacm = project.CommitteeMember(FakeWrite(), FakeWriteAs(), data, "commons")
    schedule = mock.AsyncMock()
    monkeypatch.setattr(project.tasks, "cap_approval_resolve", schedule)
    closes_at = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    approval = await wacm.request_approval(safe.ProjectKey("commons-foo"), sql.ApprovalAction.ARCHIVE, 42, closes_at)
    assert approval.status == sql.ApprovalStatus.PENDING
    assert approval.requested_by == "alice"
    schedule.assert_awaited_once()
    assert schedule.call_args.kwargs["schedule"] == closes_at + projects.constants.CAP_RESOLVE_INITIAL_DELAY
    assert schedule.call_args.kwargs["caller_data"] is data


@pytest.mark.asyncio
async def test_resolve_approved_marks_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    data = FakeData(_approval_row())
    reenqueue = _patch_resolve_env(monkeypatch, data, {"outcome": "approved", "permalink": "https://cap/x"})
    result = await cap.resolve({"approval_request_id": 1, "attempt": 0})
    assert isinstance(result, results.CapApprovalResolve)
    assert data.row is not None
    assert data.row.status == sql.ApprovalStatus.APPROVED
    assert data.row.permalink == "https://cap/x"
    reenqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_db_error_reenqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    data = FakeData(_approval_row())
    reenqueue = _patch_resolve_env(monkeypatch, data, {"outcome": "approved", "permalink": "https://cap/x"})
    data.begin_immediate = mock.AsyncMock(side_effect=sqlalchemy.exc.InvalidRequestError("database error"))
    await cap.resolve({"approval_request_id": 1, "attempt": 0})
    assert data.row is not None
    assert data.row.status == sql.ApprovalStatus.PENDING
    reenqueue.assert_awaited_once()
    assert reenqueue.call_args.kwargs["attempt"] == 1


@pytest.mark.asyncio
async def test_resolve_exhausted_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    data = FakeData(_approval_row())
    reenqueue = _patch_resolve_env(monkeypatch, data, None)
    await cap.resolve({"approval_request_id": 1, "attempt": len(cap.constants.CAP_RESOLVE_RETRY_DELAYS)})
    assert data.row is not None
    assert data.row.status == sql.ApprovalStatus.FAILED
    assert data.row.error is not None
    reenqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_last_attempt_reenqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    data = FakeData(_approval_row())
    reenqueue = _patch_resolve_env(monkeypatch, data, None)
    await cap.resolve({"approval_request_id": 1, "attempt": len(cap.constants.CAP_RESOLVE_RETRY_DELAYS) - 1})
    assert data.row is not None
    assert data.row.status == sql.ApprovalStatus.PENDING
    reenqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_missing_outcome_reenqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    data = FakeData(_approval_row())
    reenqueue = _patch_resolve_env(monkeypatch, data, {"permalink": "https://cap/x"})
    await cap.resolve({"approval_request_id": 1, "attempt": 0})
    assert data.row is not None
    assert data.row.status == sql.ApprovalStatus.PENDING
    reenqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_not_approved_marks_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    data = FakeData(_approval_row())
    _patch_resolve_env(monkeypatch, data, {"outcome": "insufficient_votes", "permalink": "https://cap/x"})
    await cap.resolve({"approval_request_id": 1, "attempt": 0})
    assert data.row is not None
    assert data.row.status == sql.ApprovalStatus.REJECTED
    assert data.row.outcome == "insufficient_votes"


@pytest.mark.asyncio
async def test_resolve_unavailable_reenqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    data = FakeData(_approval_row())
    reenqueue = _patch_resolve_env(monkeypatch, data, None)
    before = datetime.datetime.now(datetime.UTC)
    await cap.resolve({"approval_request_id": 1, "attempt": 0})
    after = datetime.datetime.now(datetime.UTC)
    assert data.row is not None
    assert data.row.status == sql.ApprovalStatus.PENDING
    reenqueue.assert_awaited_once()
    assert reenqueue.call_args.kwargs["attempt"] == 1
    assert before + cap.constants.CAP_RESOLVE_RETRY_DELAYS[0] <= reenqueue.call_args.kwargs["schedule"]
    assert reenqueue.call_args.kwargs["schedule"] <= after + cap.constants.CAP_RESOLVE_RETRY_DELAYS[0]


@pytest.mark.asyncio
async def test_resolve_unexpected_error_reenqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    data = FakeData(_approval_row())
    reenqueue = _patch_resolve_env(monkeypatch, data, {"outcome": "approved", "permalink": "https://cap/x"})
    monkeypatch.setattr(cap.util, "cap_resolve_question", mock.AsyncMock(side_effect=RuntimeError("unexpected")))
    await cap.resolve({"approval_request_id": 1, "attempt": 0})
    assert data.row is not None
    assert data.row.status == sql.ApprovalStatus.PENDING
    reenqueue.assert_awaited_once()
    assert reenqueue.call_args.kwargs["attempt"] == 1


def _approval_row(
    status: sql.ApprovalStatus = sql.ApprovalStatus.PENDING,
    action: sql.ApprovalAction = sql.ApprovalAction.ARCHIVE,
) -> sql.ApprovalRequest:
    return sql.ApprovalRequest(
        id=1,
        project_key="commons-foo",
        committee_key="commons",
        action=action,
        cap_question_id=10,
        status=status,
        requested_by="alice",
        closes_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
    )


def _patch_http(monkeypatch: pytest.MonkeyPatch, status: int, body: str) -> None:
    session = FakeSession(FakeResponse(status, body))
    monkeypatch.setattr(util, "create_secure_session", lambda **kwargs: session)


def _patch_resolve_env(
    monkeypatch: pytest.MonkeyPatch, data: FakeData, resolution: dict[str, object] | None
) -> mock.AsyncMock:
    resolution_model = None
    if resolution is not None:
        resolution_model = atr.models.cap.Resolution.model_validate(resolution)
    monkeypatch.setattr(cap.db, "session", lambda: FakeSessionCM(data))
    monkeypatch.setattr(cap.util, "cap_mint_token", mock.AsyncMock(return_value="tok"))
    monkeypatch.setattr(cap.util, "cap_resolve_question", mock.AsyncMock(return_value=resolution_model))
    monkeypatch.setattr(cap.storage, "write_as_user_service", lambda uid: FakeUserService())
    admin = project.FoundationAdmin(FakeWrite(), FakeWriteAs(), data)
    monkeypatch.setattr(cap.storage, "write_as_system", lambda cls: FakeSystemService(admin.record_approval_outcome))
    reenqueue = mock.AsyncMock()
    monkeypatch.setattr(cap.tasks, "cap_approval_resolve", reenqueue)
    return reenqueue


def _project_with_drafts(*created: datetime.datetime) -> sql.Project:
    project = sql.Project(key="commons-foo", status=sql.ProjectStatus.ACTIVE)
    project.releases_including_embargoed = [
        sql.Release(
            project_key="commons-foo",
            version=f"0.{i}",
            phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            created=created_at,
        )
        for i, created_at in enumerate(created)
    ]
    return project
