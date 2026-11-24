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
import inspect
import unittest.mock as mock
from types import SimpleNamespace

import pydantic
import pytest

import atr.db.interaction as interaction
import atr.form as form
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post.projects as post_projects
import atr.shared.projects as shared_projects
import atr.storage as storage
import atr.storage.writers.project as project_writer
import atr.upload_restrictions as upload_restrictions

_PROJECT_KEY = safe.ProjectKey("example")
_RELEASE = ".github/workflows/release.yml"
_WORKFLOW_REF = f"apache/example/{_RELEASE}@refs/heads/main"


@pytest.mark.parametrize(
    ("workflow_paths", "ref_type", "ref_patterns", "ref", "problem"),
    [
        pytest.param([], "", [], "refs/heads/anything", None, id="nothing-set"),
        pytest.param([_RELEASE], "", [], "refs/heads/anything", None, id="path"),
        pytest.param([".github/workflows/ci.yml", _RELEASE], "", [], "refs/tags/v1", None, id="one-of-two-paths"),
        pytest.param([".github/workflows/ci.yml"], "", [], "refs/heads/main", "The workflow", id="other-path"),
        pytest.param(
            [".github/workflows/release.yml.bak"], "", [], "refs/heads/main", "The workflow", id="longer-path"
        ),
        pytest.param([], "branch", [], "refs/heads/feature/x", None, id="any-branch"),
        pytest.param([], "branch", [], "refs/tags/v1", "only a run on a branch", id="tag-for-branch"),
        pytest.param([], "tag", [], "refs/heads/main", "only a run on a tag", id="branch-for-tag"),
        pytest.param([], "branch", ["main", "release/*"], "refs/heads/release/2.5", None, id="second-pattern"),
        pytest.param([], "branch", ["main", "release/*"], "refs/heads/feature", "The branch feature", id="no-pattern"),
        pytest.param([], "branch", ["release/*"], "refs/heads/release/2/5", "The branch release/2/5", id="star-slash"),
        pytest.param([], "tag", ["v*"], "refs/tags/v1.0.0", None, id="tag-pattern"),
        pytest.param([], "tag", ["v*"], "refs/tags/rel-1", "The tag rel-1", id="tag-outside-pattern"),
        pytest.param([_RELEASE], "branch", ["main"], "refs/heads/dev", "The branch dev", id="path-then-ref"),
    ],
)
def test_a_run_is_checked_against_each_restriction_that_is_set(
    workflow_paths: list[str], ref_type: str, ref_patterns: list[str], ref: str, problem: str | None
) -> None:
    options = _options(workflow_paths, ref_type, ref_patterns)

    found = upload_restrictions.problem(options, "apache/example", _WORKFLOW_REF, ref)

    if problem is None:
        assert found is None
    else:
        assert found is not None
        assert problem in found


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [True, False])
async def test_a_run_outside_the_restrictions_cannot_upload(monkeypatch: pytest.MonkeyPatch, declared: bool) -> None:
    committee = sql.Committee(key="example", name="Example", github_oidc_enabled=True)
    project = sql.Project(
        key="example",
        name="Example",
        repositories=["https://github.com/apache/example"],
        committee_key="example",
        committee=committee,
    )
    data = mock.MagicMock()
    data.project.return_value.get = mock.AsyncMock(return_value=project)
    data.project_security_options.return_value.get = mock.AsyncMock(return_value=_options([], "tag", ["v*"]))
    session = mock.AsyncMock()
    session.__aenter__.return_value = data
    monkeypatch.setattr(interaction.db, "session", mock.MagicMock(return_value=session))
    monkeypatch.setattr(interaction, "ci_builds_enabled", mock.AsyncMock(return_value=True))
    # A request with no project finds one from the workflow path, and the restrictions hold for it too
    monkeypatch.setattr(interaction, "_trusted_project", mock.AsyncMock(return_value=project))
    payload = SimpleNamespace(
        repository="apache/example", workflow_ref=_WORKFLOW_REF, ref="refs/heads/main", run_attempt="1", run_id="1"
    )

    with pytest.raises(interaction.errors.ASFQuartException) as exc_info:
        await interaction.compose_project_for_payload(payload, "alice", _PROJECT_KEY if declared else None)

    assert exc_info.value.errorcode == 403
    message = str(exc_info.value)
    assert "The run is on refs/heads/main, and only a run on a tag can upload" in message
    assert "A member of the example PMC can change the GitHub OIDC restrictions of project 'example'" in message
    assert "http" not in message


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"workflow_paths": "release.yml"}, "must start with '.github/workflows/'"),
        ({"ref_patterns": "main"}, "Choose branches or tags"),
        ({"ref_type": "Branches", "ref_patterns": "release/[!a]*"}, "Unsupported pattern"),
        ({"ref_type": "Tags", "ref_patterns": "v**"}, "Unsupported pattern"),
    ],
)
def test_form_refuses_values_that_cannot_be_matched(fields: dict[str, str], message: str) -> None:
    with pytest.raises(pydantic.ValidationError, match=message):
        form.validate(shared_projects.UploadRestrictionsForm, _form_data(**fields))


def test_form_reads_one_value_per_line() -> None:
    restrictions = form.validate(
        shared_projects.UploadRestrictionsForm,
        _form_data(
            workflow_paths=f" {_RELEASE} \r\n\r\n.github/workflows/stage.yml",
            ref_type="Branches",
            ref_patterns="main\n release/* \n",
        ),
    )

    assert isinstance(restrictions, shared_projects.UploadRestrictionsForm)
    assert restrictions.workflow_path_list() == [_RELEASE, ".github/workflows/stage.yml"]
    assert restrictions.stored_ref_type() == "branch"
    assert restrictions.ref_pattern_list() == ["main", "release/*"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("member", "release_manager", "saved"),
    [
        pytest.param(True, False, True, id="pmc-member"),
        pytest.param(False, True, False, id="release-manager"),
        pytest.param(False, False, False, id="committer"),
    ],
)
async def test_only_a_pmc_member_saves_restrictions(
    monkeypatch: pytest.MonkeyPatch, member: bool, release_manager: bool, saved: bool
) -> None:
    # The storage layer decides this for real here
    committee = sql.Committee(
        key="example",
        committers=["alice"],
        committee_members=["alice"] if member else [],
        release_managers=["alice"] if release_manager else [],
    )
    project = sql.Project(key="example", name="Example", committee=committee)
    authorisation = mock.MagicMock(asf_uid="alice")
    authorisation.is_member_of.return_value = member
    data = mock.MagicMock()
    data.project.return_value.demand = mock.AsyncMock(return_value=project)
    setter = mock.AsyncMock(return_value=True)

    @contextlib.asynccontextmanager
    async def write(_session: object):
        yield storage.Write(authorisation, data)

    monkeypatch.setattr(post_projects.storage, "write", write)
    monkeypatch.setattr(project_writer.CommitteeMember, "upload_restrictions_set", setter)
    session = mock.Mock(is_admin=False)
    session.redirect = mock.AsyncMock(return_value="redirected")
    restrictions = form.validate(
        shared_projects.UploadRestrictionsForm, _form_data(workflow_paths=_RELEASE, ref_type="Tags", ref_patterns="v*")
    )
    handler = inspect.getclosurevars(inspect.unwrap(post_projects.advanced)).nonlocals["func"]

    assert await handler(session, "project/advanced", _PROJECT_KEY, restrictions) == "redirected"

    if saved:
        setter.assert_awaited_once_with(_PROJECT_KEY, [_RELEASE], "tag", ["v*"])
        assert "success" in session.redirect.await_args.kwargs
    else:
        setter.assert_not_awaited()
        assert "Not a member of example" in session.redirect.await_args.kwargs["error"]


@pytest.mark.asyncio
async def test_writer_keeps_the_values_and_audits_the_change() -> None:
    options = _options([".github/workflows/old.yml"], "", [])
    writer, data, write_as = _writer(options)

    changed = await writer.upload_restrictions_set(_PROJECT_KEY, [_RELEASE], "branch", ["main"])

    assert changed is True
    assert options.github_upload_workflow_paths == [_RELEASE]
    assert options.github_upload_ref_type == "branch"
    assert options.github_upload_ref_patterns == ["main"]
    data.commit.assert_awaited_once()
    audit = write_as.append_to_audit_log.call_args.kwargs
    assert audit["asf_uid"] == "alice"
    assert audit["project_key"] == "example"
    assert audit["before"] == {"workflow_paths": [".github/workflows/old.yml"], "ref_type": "", "ref_patterns": []}
    assert audit["after"] == {"workflow_paths": [_RELEASE], "ref_type": "branch", "ref_patterns": ["main"]}


@pytest.mark.asyncio
async def test_writer_makes_the_row_for_a_project_without_one() -> None:
    writer, data, _write_as = _writer(None)

    assert await writer.upload_restrictions_set(_PROJECT_KEY, [], "tag", []) is True

    added = data.add.call_args.args[0]
    assert isinstance(added, sql.ProjectSecurityOptions)
    assert (added.project_key, added.github_upload_ref_type) == ("example", "tag")


@pytest.mark.asyncio
async def test_writer_refuses_values_that_cannot_be_matched() -> None:
    writer, data, write_as = _writer(None)

    with pytest.raises(storage.AccessError, match="Choose branches or tags") as exc_info:
        await writer.upload_restrictions_set(_PROJECT_KEY, [], "", ["main"])

    assert exc_info.value.status == 400
    data.commit.assert_not_awaited()
    write_as.append_to_audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_writer_writes_nothing_when_nothing_changes() -> None:
    writer, data, write_as = _writer(_options([_RELEASE], "", []))

    assert await writer.upload_restrictions_set(_PROJECT_KEY, [_RELEASE], "", []) is False

    data.commit.assert_not_awaited()
    write_as.append_to_audit_log.assert_not_called()


def _form_data(workflow_paths: str = "", ref_type: str = "Any branch or tag", ref_patterns: str = "") -> dict[str, str]:
    return {
        "csrf_token": "token",
        "workflow_paths": workflow_paths,
        "ref_type": ref_type,
        "ref_patterns": ref_patterns,
    }


def _options(workflow_paths: list[str], ref_type: str, ref_patterns: list[str]) -> sql.ProjectSecurityOptions:
    return sql.ProjectSecurityOptions(
        project_key="example",
        github_upload_workflow_paths=workflow_paths,
        github_upload_ref_type=ref_type,
        github_upload_ref_patterns=ref_patterns,
    )


def _writer(
    options: sql.ProjectSecurityOptions | None,
) -> tuple[project_writer.CommitteeMember, mock.MagicMock, SimpleNamespace]:
    project = sql.Project(key="example", name="Example", committee_key="example")
    data = mock.MagicMock()
    data.project.return_value.demand = mock.AsyncMock(return_value=project)
    data.project_security_options.return_value.get = mock.AsyncMock(return_value=options)
    data.begin_immediate = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    write_as = SimpleNamespace(append_to_audit_log=mock.MagicMock())
    writer = object.__new__(project_writer.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__write_as = write_as
    writer._CommitteeMember__asf_uid = "alice"
    writer._CommitteeMember__committee_key = "example"
    return writer, data, write_as
