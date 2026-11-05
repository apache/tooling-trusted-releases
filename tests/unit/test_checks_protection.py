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
import functools
import pathlib
import unittest.mock as mock
from typing import Any, Final

import aiohttp
import pytest

import atr.attestable as attestable
import atr.db as db
import atr.ldap as ldap
import atr.models.safe as safe
import atr.models.sql as sql
import atr.tasks as tasks
import atr.tasks.checks as checks
import atr.tasks.checks.protection as protection
import atr.tasks.task as task
import tests.unit.recorders as recorders

_BRANCH: Final = "refs/heads/main"
_COMMIT: Final = "a" * 40
_OTHER: Final = "c" * 40
_PEKKO_BYPASS_EVALUATIONS: Final[list[dict[str, Any]]] = [
    {
        "enforcement": "active",
        "result": "pass",
        "rule_source": {"type": "secret_scanning"},
        "rule_type": "secret_scanning",
    },
    {"enforcement": "active", "result": "fail", "rule_source": {"type": "ruleset"}, "rule_type": "creation"},
    {"enforcement": "active", "result": "pass", "rule_source": {"type": "ruleset"}, "rule_type": "update"},
    {"enforcement": "active", "result": "pass", "rule_source": {"type": "ruleset"}, "rule_type": "deletion"},
    {"enforcement": "active", "result": "pass", "rule_source": {"type": "ruleset"}, "rule_type": "update"},
]
_TAG: Final = "refs/tags/v1.0"
_TAG_OBJECT: Final = "b" * 40


async def test_annotated_tag_matches_its_commit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, [_suite(["creation", "update"], after_sha=_TAG_OBJECT)])
    assert "was protected" in await _run(tmp_path, _TAG, sql.CheckResultStatus.NOTE)


async def test_bypass_by_anyone_else(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, [{**_suite([], result="bypass"), "rule_evaluations": _PEKKO_BYPASS_EVALUATIONS}])
    monkeypatch.setattr(protection, "_may_bypass", mock.AsyncMock(return_value=False))
    assert "bypassed by alice" in await _run(tmp_path, _TAG, sql.CheckResultStatus.CONCERN)


async def test_check_accepts_allowed_bypass(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, [{**_suite([], result="bypass"), "rule_evaluations": _PEKKO_BYPASS_EVALUATIONS}])
    monkeypatch.setattr(protection, "_may_bypass", mock.AsyncMock(return_value=True))
    assert "was protected" in await _run(tmp_path, _TAG, sql.CheckResultStatus.NOTE)


async def test_evaluate_rule_does_not_count(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(["update"])
    suite["rule_evaluations"].append(_evaluation("creation", enforcement="evaluate"))
    _serve(monkeypatch, [suite])
    assert "no active rule for creation" in await _run(tmp_path, _TAG, sql.CheckResultStatus.CONCERN)


async def test_failure_is_a_concern(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(protection, "_github", mock.AsyncMock(side_effect=aiohttp.ClientError("down")))
    assert "Could not check" in await _run(tmp_path, _TAG, sql.CheckResultStatus.CONCERN)


@pytest.mark.parametrize(
    ("uid", "allowed"),
    [("member", True), ("manager", True), ("committer", False)],
)
async def test_may_bypass(monkeypatch: pytest.MonkeyPatch, uid: str, allowed: bool) -> None:
    project = mock.Mock()
    project.committee = sql.Committee(
        key="test",
        committee_members=["member"],
        committers=["member", "manager", "committer"],
        release_managers=["manager"],
    )
    session = mock.Mock()
    session.__aenter__ = mock.AsyncMock(return_value=session)
    session.__aexit__ = mock.AsyncMock(return_value=None)
    session.project.return_value.demand = mock.AsyncMock(return_value=project)
    monkeypatch.setattr(db, "session", mock.Mock(return_value=session))
    monkeypatch.setattr(ldap, "github_to_apache", mock.AsyncMock(return_value=uid))
    assert await protection._may_bypass(safe.ProjectKey("test"), 1) is allowed


async def test_may_not_bypass_without_asf_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ldap, "github_to_apache", mock.AsyncMock(side_effect=ldap.LookupError("unknown")))
    assert await protection._may_bypass(safe.ProjectKey("test"), 1) is False


async def test_no_record_without_suites(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, [])
    assert "no protection record" in await _run(tmp_path, _TAG, sql.CheckResultStatus.CONCERN)


async def test_older_suite_is_selected(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    newer = _suite([], after_sha=_OTHER, suite_id=1)
    older = _suite(["creation", "update"], suite_id=2)
    _serve(monkeypatch, [newer, older])
    assert "was protected" in await _run(tmp_path, _BRANCH, sql.CheckResultStatus.NOTE)


async def test_other_commit_is_no_record(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, [_suite(["creation", "update"], after_sha=_OTHER)])
    assert "no protection record" in await _run(tmp_path, _BRANCH, sql.CheckResultStatus.CONCERN)


async def test_other_tag_commit_is_no_record(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, [_suite(["creation", "update"], after_sha=_OTHER)])
    assert "no protection record" in await _run(tmp_path, _TAG, sql.CheckResultStatus.CONCERN)


async def test_rejected_push_is_no_record(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, [_suite(["creation", "update"], result="fail")])
    assert "no protection record" in await _run(tmp_path, _TAG, sql.CheckResultStatus.CONCERN)


async def test_request_names_the_ref(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    github = mock.AsyncMock(return_value=[])
    monkeypatch.setattr(protection, "_github", github)
    await _run(tmp_path, _TAG, sql.CheckResultStatus.CONCERN)
    github.assert_awaited_once_with(
        "/repos/apache/test/rulesets/rule-suites", {"ref": _TAG, "time_period": "month", "per_page": "100"}
    )


@pytest.mark.parametrize(
    ("ref", "rules", "status", "message"),
    [
        (_TAG, ["creation", "update"], sql.CheckResultStatus.NOTE, "was protected"),
        (_TAG, ["deletion", "non_fast_forward"], sql.CheckResultStatus.CONCERN, "no active rule for creation, update"),
        (_BRANCH, ["creation", "update"], sql.CheckResultStatus.NOTE, "was protected"),
        (_BRANCH, ["creation", "non_fast_forward", "pull_request"], sql.CheckResultStatus.NOTE, "was protected"),
        (_BRANCH, ["non_fast_forward", "pull_request"], sql.CheckResultStatus.CONCERN, "no active rule for creation"),
    ],
)
async def test_required_rules(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    ref: str,
    rules: list[str],
    status: sql.CheckResultStatus,
    message: str,
) -> None:
    _serve(monkeypatch, [_suite(rules)])
    assert message in await _run(tmp_path, ref, status)


async def test_task_cache_key_is_scoped_to_release(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = mock.Mock(repository="apache/test", ref=_TAG, sha=_COMMIT)
    monkeypatch.setattr(attestable, "latest_github_tp_payload", mock.AsyncMock(return_value=payload))
    monkeypatch.setattr(attestable, "load", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(attestable, "write_checks_data", mock.AsyncMock())
    first = await tasks._protection_check_task("tester", _release("1.0"), safe.RevisionNumber("00001"))
    later = await tasks._protection_check_task("tester", _release("1.0"), safe.RevisionNumber("00002"))
    other = await tasks._protection_check_task("tester", _release("2.0"), safe.RevisionNumber("00001"))
    assert (first is not None) and (later is not None) and (other is not None)
    assert first.task_args == {"repository": "apache/test", "ref": _TAG, "sha": _COMMIT}
    assert first.task_type in task.CHECK_TASK_TYPES
    assert first.inputs_hash == later.inputs_hash
    assert first.inputs_hash != other.inputs_hash


async def test_task_only_for_trusted_publishing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attestable, "latest_github_tp_payload", mock.AsyncMock(return_value=None))
    assert await tasks._protection_check_task("tester", _release("1.0"), safe.RevisionNumber("00001")) is None


def _evaluation(rule_type: str, enforcement: str = "active") -> dict[str, Any]:
    return {"enforcement": enforcement, "result": "pass", "rule_source": {"type": "ruleset"}, "rule_type": rule_type}


async def _fake_github(suites: list[dict[str, Any]], path: str, params: dict[str, str] | None = None) -> Any:
    if path.endswith("/rule-suites"):
        return suites
    if "/git/tags/" in path:
        return {"object": {"sha": _COMMIT if path.endswith(_TAG_OBJECT) else _OTHER, "type": "commit"}}
    suite_id = int(path.rsplit("/", 1)[1])
    return next(suite for suite in suites if suite["id"] == suite_id)


def _release(version: str) -> sql.Release:
    release = sql.Release(
        key=f"test-{version}",
        project_key="test",
        version=version,
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        created=datetime.datetime.now(datetime.UTC),
    )
    release.project = sql.Project(key="test", name="Apache Test")
    return release


async def _run(tmp_path: pathlib.Path, ref: str, status: sql.CheckResultStatus) -> str:
    recorder = recorders.RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.protection.check")
    args = checks.FunctionArguments(
        recorder=recorders.get_recorder(recorder),
        asf_uid="tester",
        project_key=safe.ProjectKey("test"),
        version_key=safe.VersionKey("1.0"),
        revision_number=safe.RevisionNumber("00001"),
        primary_rel_path=None,
        extra_args={"repository": "apache/test", "ref": ref, "sha": _COMMIT},
    )
    await protection.check(args)
    assert [entry[0] for entry in recorder.messages] == [status.value]
    return recorder.messages[0][1]


def _serve(monkeypatch: pytest.MonkeyPatch, suites: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(protection, "_github", functools.partial(_fake_github, suites))


def _suite(rules: list[str], after_sha: str = _COMMIT, result: str = "pass", suite_id: int = 1) -> dict[str, Any]:
    return {
        "actor_id": 1,
        "actor_name": "alice",
        "after_sha": after_sha,
        "id": suite_id,
        "result": result,
        "rule_evaluations": [_evaluation(rule) for rule in rules],
    }
