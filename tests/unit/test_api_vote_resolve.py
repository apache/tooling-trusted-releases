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
import datetime
import inspect
import secrets
import unittest.mock as mock
from types import SimpleNamespace

import pydantic
import pytest
import quart
import quart_schema
import sqlalchemy.ext.asyncio as asyncio_ext
import sqlmodel

import atr.api as api
import atr.blueprints.api as api_blueprint
import atr.db as db
import atr.db.interaction as interaction
import atr.jwtoken as jwtoken
import atr.models.api as models_api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.tabulate as models_tabulate
import atr.principal as principal
import atr.storage as storage
import atr.user as user
import atr.util as util


class MockQuery:
    def __init__(self, value: object) -> None:
        self._value = value

    async def demand(self, _error: Exception) -> object:
        if self._value is None:
            raise _error
        return self._value

    async def get(self) -> object:
        return self._value


class MockDBSession:
    def __init__(self, release: object, project: object = None, security_options: object = None) -> None:
        self._release = release
        self._project = project
        self._security_options = security_options

    def project(self, **_kwargs: object) -> MockQuery:
        return MockQuery(self._project)

    def project_security_options(self, **_kwargs: object) -> MockQuery:
        return MockQuery(self._security_options)

    def release(self, **_kwargs: object) -> MockQuery:
        return MockQuery(self._release)


@pytest.mark.asyncio
async def test_policy_get_and_update_without_vote_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    app, headers = _api_test_app(monkeypatch, "chair")
    project = _mock_policy_project()
    _patch_session(monkeypatch, release=None, project=project)
    warm = mock.MagicMock()
    warm.policy.edit_policy = mock.AsyncMock()
    monkeypatch.setattr(storage, "write_as_project_release_manager", _async_cm_factory(warm))

    get_resp = await app.test_client().get("/api/policy/get/example")
    assert get_resp.status_code == 200
    get_json = await get_resp.get_json()
    assert "policy_vote_mode" not in get_json
    models_api.PolicyGetResults.model_validate(get_json)

    bad_update = await app.test_client().post(
        "/api/policy/update",
        headers=headers,
        json={"project": "example", "vote_mode": "trusted"},
    )
    assert bad_update.status_code == 400

    ok_update = await app.test_client().post(
        "/api/policy/update",
        headers=headers,
        json={"project": "example", "min_hours": 72},
    )
    assert ok_update.status_code == 200
    warm.policy.edit_policy.assert_awaited_once()


@pytest.mark.asyncio
async def test_publisher_vote_resolve_delegates_to_vote_resolver_unattended(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, release=SimpleNamespace(is_embargoed=False))
    wavr = mock.MagicMock()
    wavr.resolve = mock.AsyncMock(return_value=(None, None, "", None))
    write_resolver = _async_cm_factory(wavr)
    context = SimpleNamespace(payload={}, asf_uid="user")
    project = SimpleNamespace(safe_key=safe.ProjectKey("solr-mcp"))
    monkeypatch.setattr(api.api.auth, "trusted_publisher_context", lambda: context)
    monkeypatch.setattr(api.interaction, "trusted_project_for_payload", mock.AsyncMock(return_value=("user", project)))
    monkeypatch.setattr(api, "_ldap_fullname", mock.AsyncMock(return_value="Example User"))
    monkeypatch.setattr(storage, "write_as_vote_resolver", write_resolver)

    result, status = await inspect.unwrap(api.publisher_vote_resolve)(
        "publisher/vote/resolve", SimpleNamespace(version=safe.VersionKey("1.0.0"), resolution="passed")
    )

    assert status == 200
    assert result == {"endpoint": "/publisher/vote/resolve", "success": True}
    write_resolver.assert_called_once_with(safe.ProjectKey("solr-mcp"), safe.VersionKey("1.0.0"), "user")
    assert wavr.resolve.await_args.args == (
        safe.ProjectKey("solr-mcp"),
        safe.VersionKey("1.0.0"),
        "passed",
        "Example User",
        None,
    )
    assert wavr.resolve.await_args.kwargs == {"unattended": True}


@pytest.mark.asyncio
async def test_publisher_vote_resolve_unattended_guard_maps_409(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, release=SimpleNamespace(is_embargoed=False))
    wavr = mock.MagicMock()
    wavr.resolve = mock.AsyncMock(
        side_effect=storage.AccessError("Unattended resolution requires unanimous yes votes", status=409)
    )
    context = SimpleNamespace(payload={}, asf_uid="user")
    project = SimpleNamespace(safe_key=safe.ProjectKey("example"))
    monkeypatch.setattr(api.api.auth, "trusted_publisher_context", lambda: context)
    monkeypatch.setattr(api.interaction, "trusted_project_for_payload", mock.AsyncMock(return_value=("user", project)))
    monkeypatch.setattr(storage, "write_as_vote_resolver", _async_cm_factory(wavr))

    with pytest.raises(api.exceptions.Conflict, match="unanimous yes votes"):
        await inspect.unwrap(api.publisher_vote_resolve)(
            "publisher/vote/resolve", SimpleNamespace(version=safe.VersionKey("1.0.0"), resolution="passed")
        )


@pytest.mark.asyncio
async def test_vote_cast_forwards_vote_seq_and_returns_receipt_or_409(monkeypatch: pytest.MonkeyPatch) -> None:
    app, headers = _api_test_app(monkeypatch, "alice")
    _patch_session(monkeypatch, release=SimpleNamespace(is_embargoed=False))
    wafc = mock.MagicMock()
    wafc.vote.cast = mock.AsyncMock(return_value=(["dev@example.apache.org"], "<receipt@apache.org>"))
    write = mock.MagicMock()
    write.as_foundation_committer.return_value = wafc
    monkeypatch.setattr(storage, "write", _async_cm_factory(write))

    response = await app.test_client().post(
        "/api/vote/cast",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "choice": "+1", "vote_seq": 2},
    )
    assert response.status_code == 200
    assert await response.get_json() == {
        "endpoint": "/vote/cast",
        "success": True,
        "receipt_email_to": ["dev@example.apache.org"],
    }
    assert wafc.vote.cast.await_args.kwargs == {"expected_vote_seq": 2}

    wafc.vote.cast = mock.AsyncMock(side_effect=storage.AccessError("Vote sequence mismatch", status=409))
    conflict = await app.test_client().post(
        "/api/vote/cast",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "choice": "+1", "vote_seq": 1},
    )
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_vote_confirm_access_matrix_for_pmc_rm_and_round2_ipmc(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = asyncio_ext.create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    maker = asyncio_ext.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    async with maker() as data:
        committee = sql.Committee(
            key="pod",
            name="Podling",
            is_podling=True,
            committee_members=["ppmc"],
            committers=["ppmc", "rm"],
            release_managers=["rm"],
        )
        project = sql.Project(key="pod", name="Apache Podling", committee=committee)
        data.add(
            sql.Release(
                project=project,
                project_key="pod",
                version="1.0.0",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                podling_thread_id=None,
                created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            )
        )
        await data.commit()
    monkeypatch.setattr(db, "session", maker)

    await _check_confirm_and_resolve_access("ppmc", "pod", True, True, monkeypatch)
    await _check_confirm_and_resolve_access("rm", "", False, True, monkeypatch)
    await _check_confirm_and_resolve_access("ipmc", "incubator", False, False, monkeypatch)

    async with maker() as data:
        release = await data.release(project_key="pod", version="1.0.0").demand(RuntimeError("missing"))
        release.podling_thread_id = "thread-2"
        await data.commit()

    await _check_confirm_and_resolve_access("ppmc", "pod", False, True, monkeypatch)
    await _check_confirm_and_resolve_access("rm", "", False, True, monkeypatch)
    await _check_confirm_and_resolve_access("ipmc", "incubator", True, True, monkeypatch)
    await engine.dispose()


def test_vote_confirm_args_requires_choice_and_allows_explicit_null() -> None:
    with pytest.raises(pydantic.ValidationError):
        models_api.VoteConfirmArgs.model_validate({"project": "example", "version": "1.0.0", "asf_eid": "eid-1"})

    confirmed = models_api.VoteConfirmArgs.model_validate(
        {"project": "example", "version": "1.0.0", "asf_eid": "eid-1", "choice": "+1", "vote_seq": 3}
    )
    assert confirmed.choice == sql.VoteChoice.YES
    assert confirmed.vote_seq == 3

    unconfirmed = models_api.VoteConfirmArgs.model_validate(
        {"project": "example", "version": "1.0.0", "asf_eid": "eid-1", "choice": None}
    )
    assert unconfirmed.choice is None


@pytest.mark.asyncio
async def test_vote_confirm_confirm_unconfirm_embargo_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    app, headers = _api_test_app(monkeypatch, "chair")
    release = SimpleNamespace(is_embargoed=False)
    _patch_session(monkeypatch, release=release)
    wacm = mock.MagicMock()
    wacm.vote.confirm_email_vote = mock.AsyncMock()
    write_cm = _async_cm_factory(wacm)
    monkeypatch.setattr(storage, "write_as_vote_committee_member", write_cm)

    resp_ok = await app.test_client().post(
        "/api/vote/confirm",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "asf_eid": "eid-1", "choice": "+1", "vote_seq": 2},
    )
    assert resp_ok.status_code == 200
    assert await resp_ok.get_json() == {"endpoint": "/vote/confirm", "success": True}
    write_cm.assert_called_once_with(safe.ProjectKey("example"), safe.VersionKey("1.0.0"), "chair")
    assert wacm.vote.confirm_email_vote.await_args.args == (
        safe.ProjectKey("example"),
        safe.VersionKey("1.0.0"),
        "eid-1",
        sql.VoteChoice.YES,
    )
    assert wacm.vote.confirm_email_vote.await_args.kwargs == {"expected_vote_seq": 2}

    resp_reset = await app.test_client().post(
        "/api/vote/confirm",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "asf_eid": "eid-1", "choice": None},
    )
    assert resp_reset.status_code == 200
    assert wacm.vote.confirm_email_vote.await_args.args[3] is None

    wacm.vote.confirm_email_vote = mock.AsyncMock(
        side_effect=interaction.InteractionError("Message not found in thread")
    )
    resp_404 = await app.test_client().post(
        "/api/vote/confirm",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "asf_eid": "eid-missing", "choice": "-1"},
    )
    assert resp_404.status_code == 404

    wacm.vote.confirm_email_vote = mock.AsyncMock(
        side_effect=interaction.InteractionError("Cannot confirm receipt email")
    )
    resp_400 = await app.test_client().post(
        "/api/vote/confirm",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "asf_eid": "eid-receipt", "choice": "0"},
    )
    assert resp_400.status_code == 400

    wacm.vote.confirm_email_vote = mock.AsyncMock(side_effect=storage.AccessError("Round changed", status=409))
    resp_409 = await app.test_client().post(
        "/api/vote/confirm",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "asf_eid": "eid-1", "choice": "+1"},
    )
    assert resp_409.status_code == 409

    wacm.vote.confirm_email_vote = mock.AsyncMock(
        side_effect=util.FetchError("lists.apache.org unavailable", "https://lists.apache.org/api/stats.lua")
    )
    resp_502 = await app.test_client().post(
        "/api/vote/confirm",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "asf_eid": "eid-1", "choice": "+1"},
    )
    assert resp_502.status_code == 502

    wacm.vote.confirm_email_vote = mock.AsyncMock(side_effect=ValueError("Invalid thread ID"))
    resp_invalid_400 = await app.test_client().post(
        "/api/vote/confirm",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "asf_eid": "eid-1", "choice": "+1"},
    )
    assert resp_invalid_400.status_code == 400

    release.is_embargoed = True
    monkeypatch.setattr(user, "embargo_denies", lambda *_args, **_kwargs: True)
    resp_embargo = await app.test_client().post(
        "/api/vote/confirm",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "asf_eid": "eid-1", "choice": "+1"},
    )
    assert resp_embargo.status_code == 404


@pytest.mark.asyncio
async def test_vote_resolve_uses_vote_resolver_and_atomic_tally(monkeypatch: pytest.MonkeyPatch) -> None:
    app, headers = _api_test_app(monkeypatch, "rm")
    _patch_session(monkeypatch, release=SimpleNamespace(is_embargoed=False))
    wavr = mock.MagicMock()
    wavr.resolve = mock.AsyncMock(return_value=(None, None, "", None))
    write_resolver = _async_cm_factory(wavr)
    monkeypatch.setattr(storage, "write_as_vote_resolver", write_resolver)

    response = await app.test_client().post(
        "/api/vote/resolve",
        headers=headers,
        json={
            "project": "example",
            "version": "1.0.0",
            "resolution": "passed",
            "early_reason": "security fix",
            "vote_seq": 3,
        },
    )
    assert response.status_code == 200
    assert await response.get_json() == {"endpoint": "/vote/resolve", "success": True}
    write_resolver.assert_called_once_with(safe.ProjectKey("example"), safe.VersionKey("1.0.0"), "rm")
    assert wavr.resolve.await_args.args == (
        safe.ProjectKey("example"),
        safe.VersionKey("1.0.0"),
        "passed",
        "Example User",
        None,
    )
    assert wavr.resolve.await_args.kwargs == {"expected_vote_seq": 3, "early_reason": "security fix"}


@pytest.mark.asyncio
async def test_vote_start_denies_embargoed_release_early(monkeypatch: pytest.MonkeyPatch) -> None:
    app, headers = _api_test_app(monkeypatch, "outsider")
    release = SimpleNamespace(is_embargoed=True, latest_revision_number="00001")
    _patch_session(monkeypatch, release=release)
    monkeypatch.setattr(user, "embargo_denies", lambda *_args, **_kwargs: True)
    write_rm = mock.MagicMock()
    monkeypatch.setattr(storage, "write_as_project_release_manager", write_rm)

    response = await app.test_client().post(
        "/api/vote/start",
        headers=headers,
        json={"project": "example", "version": "1.0.0", "email_to": "dev@example.apache.org"},
    )
    assert response.status_code == 404
    write_rm.assert_not_called()


@pytest.mark.asyncio
async def test_vote_tabulate_bearer_jwt_and_quart_schema_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    app, headers = _api_test_app(monkeypatch, "chair")
    release = SimpleNamespace(
        key="example-1.0.0",
        committee=SimpleNamespace(key="example"),
        current_vote_seq=2,
        expedited=False,
    )
    _patch_session(monkeypatch, release=release)
    counted = [
        interaction.CountedVoteDetail(
            asf_eid=None,
            cast_at=datetime.datetime(2026, 5, 1, 12, 0, tzinfo=datetime.UTC),
            choice=sql.VoteChoice.YES,
            comment="",
            confirmed_by=None,
            is_binding=True,
            receipt_message_id="<receipt@apache.org>",
            revision_number_at_cast=safe.RevisionNumber("00005"),
            source="atr",
            status_label="PMC Member (binding)",
            voter_asf_uid="alice",
            voter_email=None,
            voter_fullname="Alice",
            vote_round=None,
        ),
        interaction.CountedVoteDetail(
            asf_eid="eid-2",
            cast_at=datetime.datetime(2026, 5, 1, 13, 0, tzinfo=datetime.UTC),
            choice=sql.VoteChoice.NO,
            comment="",
            confirmed_by="chair",
            is_binding=False,
            receipt_message_id=None,
            revision_number_at_cast=None,
            source="email",
            status_label="Contributor (non-binding)",
            voter_asf_uid=None,
            voter_email="bob@example.org",
            voter_fullname="Bob",
            vote_round=1,
        ),
    ]
    summary = interaction.CountedVoteSummary(binding_votes_yes=3, non_binding_votes_no=1)
    details = models_tabulate.VoteDetails(
        start_unixtime=1714435200,
        votes={
            "bob@example.org": models_tabulate.VoteEmail(
                name="Bob",
                asf_uid_or_email="bob@example.org",
                from_email="bob@example.org",
                status=models_tabulate.VoteStatus.CONTRIBUTOR,
                asf_eid="eid-2",
                iso_datetime="2026-05-01T13:00:00Z",
                vote=models_tabulate.Vote.NO,
                quotation="-1",
                updated=True,
                confirmed=True,
                confirmed_by="chair",
            )
        },
        summary={"bob@example.org": -1},
    )
    write = mock.MagicMock()
    write.as_general_public.return_value.cache.get_message_archive_url = mock.AsyncMock(
        return_value="https://lists.apache.org/thread.html/thread-2"
    )
    write_factory = _async_cm_factory(write)
    monkeypatch.setattr(storage, "write", write_factory)
    monkeypatch.setattr(
        interaction, "counted_vote_details_and_summary", mock.AsyncMock(return_value=(counted, summary))
    )
    monkeypatch.setattr(interaction, "release_current_vote_task", mock.AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(interaction, "task_mid_get", lambda _task: "<vote@apache.org>")
    monkeypatch.setattr(interaction, "task_recipient_get", lambda _task: "dev@example.apache.org")
    monkeypatch.setattr(interaction, "ballot_receipt_message_ids", mock.AsyncMock(return_value={"receipt"}))
    monkeypatch.setattr(api.tabulate, "vote_committee", mock.AsyncMock(return_value=release.committee))
    monkeypatch.setattr(api.tabulate, "vote_details", mock.AsyncMock(return_value=details))

    response = await app.test_client().post(
        "/api/vote/tabulate",
        headers=headers,
        json={"project": "example", "version": "1.0.0"},
    )
    assert response.status_code == 200
    payload = await response.get_json()
    validated = models_api.validate_vote_tabulate(payload)
    assert validated.vote_seq == 2
    assert [entry.source for entry in validated.ballots] == ["atr", "email"]
    assert validated.ballots[0].revision_number_at_cast == safe.RevisionNumber("00005")
    assert validated.ballots[1].confirmed_by == "chair"
    assert validated.passed is True
    assert validated.details == details
    write_factory.assert_called_once_with("chair")


def _api_test_app(monkeypatch: pytest.MonkeyPatch, asf_uid: str) -> tuple[quart.Quart, dict[str, str]]:
    signing_key = secrets.token_hex(32)
    monkeypatch.setattr(jwtoken, "_signing_key", lambda: signing_key)
    monkeypatch.setattr(api.ldap, "is_active", mock.AsyncMock(return_value=True))
    monkeypatch.setattr(api, "_ldap_fullname", mock.AsyncMock(return_value="Example User"))
    monkeypatch.setattr(api.config, "get", lambda: SimpleNamespace(ADMIN_ONLY=False))
    monkeypatch.setattr(api.api.common, "validate_safe_fields", mock.AsyncMock())
    monkeypatch.setattr(user, "embargo_denies", lambda *_args, **_kwargs: False)

    app = quart.Quart(__name__)
    app.config["TESTING"] = True
    app.config["QUART_SCHEMA_PYDANTIC_DUMP_OPTIONS"] = {"mode": "json"}
    quart_schema.QuartSchema(app)
    api_blueprint.register(app)
    token = jwtoken.issue(asf_uid)
    return app, {"Authorization": f"Bearer {token}"}


def _async_cm_factory(value: object) -> mock.MagicMock:
    cm = mock.MagicMock()
    cm.__aenter__ = mock.AsyncMock(return_value=value)
    cm.__aexit__ = mock.AsyncMock(return_value=False)
    return mock.MagicMock(return_value=cm)


async def _check_confirm_and_resolve_access(
    asf_uid: str,
    committee_membership: str,
    expect_confirm: bool,
    expect_resolve: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_authorisation(uid: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            asf_uid=uid,
            is_member_of=lambda key: key == committee_membership,
            is_participant_of=lambda key: key == committee_membership,
        )

    monkeypatch.setattr(principal, "Authorisation", fake_authorisation)
    async with storage.write(asf_uid) as write:
        confirm_outcome = await write.as_vote_committee_member_outcome(safe.ProjectKey("pod"), safe.VersionKey("1.0.0"))
        resolve_outcome = await write.as_vote_resolver_outcome(safe.ProjectKey("pod"), safe.VersionKey("1.0.0"))
    assert confirm_outcome.ok is expect_confirm
    assert resolve_outcome.ok is expect_resolve


def _mock_policy_project() -> SimpleNamespace:
    return SimpleNamespace(
        safe_key=safe.ProjectKey("example"),
        policy_announce_release_subject="",
        policy_announce_release_template="",
        policy_announce_release_template_url="",
        policy_binary_artifact_paths=[],
        policy_github_finish_workflow_path=[],
        policy_github_repository_name="",
        policy_github_vote_workflow_path=[],
        policy_license_check_mode=sql.LicenseCheckMode.BOTH,
        policy_recipients=lambda _action: ("", [], []),
        policy_min_hours=72,
        policy_release_checklist="",
        policy_source_artifact_paths=[],
        policy_start_vote_subject="",
        policy_start_vote_template="",
        policy_start_vote_template_url="",
        policy_finish_vote_template="",
        policy_finish_vote_template_url="",
        policy_vote_comment_template="",
        policy_vote_comment_template_url="",
    )


def _patch_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    release: object,
    project: object = None,
    security_options: object = None,
) -> None:
    @contextlib.asynccontextmanager
    async def fake_session():
        yield MockDBSession(release, project=project, security_options=security_options)

    monkeypatch.setattr(db, "session", fake_session)
