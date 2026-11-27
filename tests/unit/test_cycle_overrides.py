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
import pathlib
import unittest.mock as mock
from collections.abc import AsyncIterator

import pydantic
import pytest
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.construct as construct
import atr.cycles as cycles
import atr.db as db
import atr.db.interaction as interaction
import atr.models.api as api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared.projects as shared_projects
import atr.storage as storage
import atr.storage.writers.policy as policy_writer
import atr.util as util

# conftest stubs this in every test, and the tests here use the real function
_RELEASE_SETTINGS = interaction.release_settings

_PROJECT = safe.ProjectKey("example")
_PATTERN = r"^(\d+\.\d+)\..*$"
_VOTE_TEMPLATE = "Project vote template for {{VERSION}}"


@pytest.fixture
async def data(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[db.Session]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cycles.db")
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    monkeypatch.setattr(interaction, "release_settings", _RELEASE_SETTINGS)
    async with db.Session(engine, expire_on_commit=False) as session:
        project = sql.Project(
            key="example",
            name="Apache Example",
            committee=sql.Committee(key="alpha", name="Alpha"),
            version_method=sql.VersionMethod.SEMVER,
            cycle_match=_PATTERN,
            release_policy=sql.ReleasePolicy(
                start_vote_template=_VOTE_TEMPLATE,
                min_hours=96,
                recipient_defaults={"vote": {"to": "dev@alpha.apache.org", "cc": [], "bcc": []}},
            ),
        )
        session.add(project)
        for version in ("10.1.5", "9.0.1"):
            cycle = version.rpartition(".")[0]
            session.add(sql.ProjectCycle(cycle_key=f"example-{cycle}", cycle=cycle, project_key="example"))
            session.add(_release(version, cycle))
        await session.commit()
        yield session
    await engine.dispose()


async def test_clearing_one_setting_leaves_the_others(data: db.Session) -> None:
    writer = _writer(data)
    await writer.cycle_overrides_edit(_vote_form("10.1", start_vote_template="Ten one template", min_hours=120))

    await writer.cycle_overrides_clear(_PROJECT, "10.1", "min_hours")

    assert await _stored(data, "10.1") == {"start_vote_template": "Ten one template"}


async def test_clearing_the_last_setting_removes_the_row(data: db.Session) -> None:
    writer = _writer(data)
    await writer.cycle_overrides_edit(_vote_form("10.1", min_hours=120))

    await writer.cycle_overrides_clear(_PROJECT, "10.1", "min_hours")

    assert await data.cycle_overrides(project_key="example").all() == []


@pytest.mark.parametrize(
    ("cycle_match", "version", "expected"),
    [
        (_PATTERN, "10.1.5", "10.1"),
        # A project with one cycle, and a version the pattern doesn't place, are both in the catch-all
        (None, "10.1.5", ""),
        (_PATTERN, "nightly", ""),
    ],
)
def test_cycle_variable_is_the_cycle_of_the_version(cycle_match: str | None, version: str, expected: str) -> None:
    project = sql.Project(key="example", cycle_match=cycle_match)

    assert cycles.cycle_text(project, version) == expected


@pytest.mark.parametrize(
    "names",
    [
        construct.ANNOUNCE_SUBJECT_VARIABLE_NAMES,
        construct.ANNOUNCE_VARIABLE_NAMES,
        construct.CHECKLIST_VARIABLE_NAMES,
        construct.FINISH_VOTE_VARIABLE_NAMES,
        construct.VOTE_SUBJECT_VARIABLE_NAMES,
        construct.VOTE_VARIABLE_NAMES,
    ],
)
def test_cycle_variable_is_valid_in_every_template(names: frozenset[str]) -> None:
    assert construct.validate_template_variables("{{CYCLE}}", names) == "{{CYCLE}}"


async def test_edit_keeps_only_what_differs_from_the_project(data: db.Session) -> None:
    await _writer(data).cycle_overrides_edit(_vote_form("10.1", start_vote_template="Ten one template"))

    # The form also carried the project's minimum hours and recipients, which are not overrides
    assert await _stored(data, "10.1") == {"start_vote_template": "Ten one template"}


async def test_edit_of_a_cycle_no_release_is_in_yet_is_stored(data: db.Session) -> None:
    await _writer(data).cycle_overrides_edit(_vote_form("11.0", min_hours=120))

    assert await _stored(data, "11.0") == {"min_hours": 120}


async def test_edit_of_a_cycle_the_version_scheme_does_not_allow_is_refused(data: db.Session) -> None:
    with pytest.raises(storage.AccessError, match="cannot have settings of its own"):
        await _writer(data).cycle_overrides_edit(_vote_form("banana", min_hours=120))


async def test_edit_of_the_default_cycle_is_refused(data: db.Session) -> None:
    with pytest.raises(storage.AccessError, match="cannot have settings of its own"):
        await _writer(data).cycle_overrides_edit(_vote_form("default", min_hours=120))


async def test_edit_records_who_made_it(data: db.Session) -> None:
    await _writer(data).cycle_overrides_edit(_vote_form("10.1", min_hours=120))

    overrides = await data.cycle_overrides(project_key="example", cycle="10.1").demand(AssertionError("Missing"))
    assert overrides.updated is not None
    assert overrides.updated_by_display == "alice"


async def test_edit_set_back_to_the_project_value_stops_overriding(data: db.Session) -> None:
    writer = _writer(data)
    await writer.cycle_overrides_edit(_vote_form("10.1", start_vote_template="Ten one template", min_hours=120))

    await writer.cycle_overrides_edit(_vote_form("10.1", start_vote_template=_VOTE_TEMPLATE, min_hours=120))

    assert await _stored(data, "10.1") == {"min_hours": 120}


async def test_edit_stores_recipients_for_the_one_action(data: db.Session) -> None:
    await _writer(data).cycle_overrides_edit(_vote_form("10.1", email_to="dev-10@alpha.apache.org"))

    assert await _stored(data, "10.1") == {
        "recipient_defaults": {"vote": {"to": "dev-10@alpha.apache.org", "cc": [], "bcc": []}}
    }


async def test_move_hands_the_overrides_to_another_cycle(data: db.Session) -> None:
    writer = _writer(data)
    await writer.cycle_overrides_edit(_vote_form("10.1", min_hours=120))

    await writer.cycle_overrides_move(_PROJECT, "10.1", "9.0")

    assert await _stored(data, "9.0") == {"min_hours": 120}
    assert await data.cycle_overrides(project_key="example", cycle="10.1").get() is None


async def test_move_onto_a_cycle_with_overrides_is_refused(data: db.Session) -> None:
    writer = _writer(data)
    await writer.cycle_overrides_edit(_vote_form("10.1", min_hours=120))
    await writer.cycle_overrides_edit(_vote_form("9.0", min_hours=100))

    with pytest.raises(storage.AccessError, match="has settings of its own"):
        await writer.cycle_overrides_move(_PROJECT, "10.1", "9.0")


async def test_overridden_policy_reads_back_as_a_release_policy(data: db.Session) -> None:
    await _writer(data).cycle_overrides_edit(_vote_form("10.1", min_hours=120))
    release = await data.release(key="example-10.1.5", _project=True).demand(AssertionError("Missing"))

    policy = (await interaction.release_settings(release, data)).release_policy

    # A revision stores this dump, and the checks read it back
    assert sql.ReleasePolicy.model_validate(policy.model_dump()).min_hours == 120


async def test_a_recipient_that_is_not_a_list_is_refused_for_a_cycle(data: db.Session) -> None:
    data.add(sql.MailingList(domain="alpha.apache.org", label="dev"))
    await data.commit()

    with pytest.raises(storage.AccessError, match="is not a mailing list"):
        await _writer(data).cycle_overrides_edit(_vote_form("10.1", email_to="userz@alpha.apache.org"))
    with pytest.raises(storage.AccessError, match="is not a mailing list"):
        await _sync(data, {"10.1": {"vote_recipients": {"to": "userz@alpha.apache.org"}}})


async def test_a_stored_recipient_that_is_not_a_list_does_not_stop_a_save(data: db.Session) -> None:
    writer = _writer(data)
    # With no lists known yet, any address on the committee's domain is accepted
    await writer.cycle_overrides_edit(_vote_form("10.1", email_to="userz@alpha.apache.org"))
    data.add(sql.MailingList(domain="alpha.apache.org", label="dev"))
    await data.commit()

    await writer.cycle_overrides_edit(_vote_form("10.1", email_to="userz@alpha.apache.org", min_hours=120))

    assert (await _stored(data, "10.1"))["min_hours"] == 120
    with pytest.raises(storage.AccessError, match="is not a mailing list"):
        await writer.cycle_overrides_edit(_vote_form("10.1", email_to="usery@alpha.apache.org"))


async def test_a_recipient_set_for_a_cycle_is_permitted_for_its_releases_only(data: db.Session) -> None:
    await _writer(data).cycle_overrides_edit(_vote_form("10.1", email_to="users@alpha.apache.org"))
    in_cycle = await data.release(key="example-10.1.5", _project=True).demand(AssertionError("Missing"))
    elsewhere = await data.release(key="example-9.0.1", _project=True).demand(AssertionError("Missing"))

    for_cycle = util.permitted_voting_recipients(
        "alice", "alpha.apache.org", settings=await interaction.release_settings(in_cycle, data)
    )
    for_other_cycle = util.permitted_voting_recipients(
        "alice", "alpha.apache.org", settings=await interaction.release_settings(elsewhere, data)
    )

    assert "users@alpha.apache.org" in for_cycle
    assert "users@alpha.apache.org" not in for_other_cycle


async def test_override_applies_to_a_release_in_its_cycle(data: db.Session) -> None:
    await _writer(data).cycle_overrides_edit(_vote_form("10.1", start_vote_template="Ten one template"))

    settings = await interaction.release_settings(await _release_row(data, "10.1.5"), data)

    assert settings.policy_start_vote_template == "Ten one template"
    # What the cycle doesn't override comes from the project
    assert settings.policy_min_hours == 96
    assert settings.policy_recipients(sql.RecipientAction.VOTE)[0] == "dev@alpha.apache.org"


async def test_override_does_not_reach_another_cycle(data: db.Session) -> None:
    await _writer(data).cycle_overrides_edit(_vote_form("10.1", start_vote_template="Ten one template"))

    settings = await interaction.release_settings(await _release_row(data, "9.0.1"), data)

    assert settings.policy_start_vote_template == _VOTE_TEMPLATE


async def test_override_follows_its_text_when_the_pattern_changes(data: db.Session) -> None:
    await _writer(data).cycle_overrides_edit(_vote_form("10.1", min_hours=120))
    project = await data.project(key="example").demand(AssertionError("Missing project"))

    # With one capture for the major version alone, 10.1.5 is in cycle 10 and nothing is in 10.1
    project.cycle_match = r"^(\d+)\..*$"
    await cycles.reassign_release_cycles(data, project)
    await data.commit()

    assert (await interaction.release_settings(await _release_row(data, "10.1.5"), data)).policy_min_hours == 96
    assert [overrides.cycle for overrides in await interaction.cycle_overrides_unmatched("example", data)] == ["10.1"]

    # Back on the first pattern, the same row applies again without being touched
    project.cycle_match = _PATTERN
    await cycles.reassign_release_cycles(data, project)
    await data.commit()

    assert (await interaction.release_settings(await _release_row(data, "10.1.5"), data)).policy_min_hours == 120
    assert await interaction.cycle_overrides_unmatched("example", data) == []


async def test_override_is_used_for_a_version_with_no_release_yet(data: db.Session) -> None:
    await _writer(data).cycle_overrides_edit(_vote_form("10.1", min_hours=120))

    assert (await interaction.version_settings("example", "10.1.6", data)).policy_min_hours == 120
    assert (await interaction.version_settings("example", "11.0.0", data)).policy_min_hours == 96


async def test_remove_drops_every_override_of_the_cycle(data: db.Session) -> None:
    writer = _writer(data)
    await writer.cycle_overrides_edit(_vote_form("10.1", start_vote_template="Ten one template", min_hours=120))

    await writer.cycle_overrides_remove(_PROJECT, "10.1")

    assert await data.cycle_overrides(project_key="example").all() == []
    assert (await interaction.release_settings(await _release_row(data, "10.1.5"), data)).policy_min_hours == 96


async def test_sync_gives_a_cycle_a_blank_value_where_the_project_has_one(data: db.Session) -> None:
    project = await data.project(key="example", _release_policy=True).demand(AssertionError("Missing"))
    if project.release_policy is None:
        raise AssertionError("Missing policy")
    project.release_policy.source_excludes_rat = ["docs/**"]
    await data.commit()

    await _sync(data, {"10.1": {"source_excludes_rat": [], "start_vote_template": "", "vote_recipients": {}}})

    assert await _stored(data, "10.1") == {
        "recipient_defaults": {"vote": {"to": "", "cc": [], "bcc": []}},
        "source_excludes_rat": [],
        "start_vote_template": "",
    }


async def test_sync_makes_the_given_cycles_the_only_ones(data: db.Session) -> None:
    await _sync(data, {"10.1": {"min_hours": 120, "start_vote_template": "Ten one"}, "9.0": {"min_hours": 100}})

    changed = await _sync(data, {"10.1": {"min_hours": 130}})

    # What a cycle is not given, and a cycle that is not given, go back to the project's settings
    assert changed == ["10.1", "9.0"]
    assert await _stored(data, "10.1") == {"min_hours": 130}
    assert await data.cycle_overrides(project_key="example", cycle="9.0").get() is None


async def test_sync_given_no_cycles_removes_every_cycle_setting(data: db.Session) -> None:
    await _sync(data, {"10.1": {"min_hours": 120}, "9.0": {"min_hours": 100}})

    changed = await _sync(data, {})

    assert changed == ["10.1", "9.0"]
    assert await data.cycle_overrides(project_key="example").all() == []


async def test_sync_null_is_the_project_value(data: db.Session) -> None:
    await _sync(data, {"10.1": {"min_hours": None, "start_vote_template": "Ten one"}})

    assert await _stored(data, "10.1") == {"start_vote_template": "Ten one"}


async def test_sync_records_how_the_settings_arrived(data: db.Session) -> None:
    await _sync(data, {"10.1": {"min_hours": 120}})

    overrides = await data.cycle_overrides(project_key="example", cycle="10.1").demand(AssertionError("Missing"))
    assert (overrides.updated_by, overrides.update_type) == ("system", sql.UpdateType.ASFYAML)


async def test_sync_refuses_a_cycle_the_version_scheme_does_not_allow(data: db.Session) -> None:
    with pytest.raises(ValueError, match="Cycle banana cannot have settings of its own"):
        await _sync(data, {"banana": {"min_hours": 120}})


def test_sync_refuses_a_setting_that_applies_to_every_cycle() -> None:
    with pytest.raises(pydantic.ValidationError, match="apply to every cycle"):
        api.ProjectConfigCycleArgs.model_validate({"binary_artifact_paths": ["bin/**"]})


async def test_writer_refuses_a_project_of_another_committee(data: db.Session) -> None:
    with pytest.raises(storage.AccessError, match="not in committee"):
        await _writer(data, committee_key="beta").cycle_overrides_edit(_vote_form("10.1", min_hours=120))


def _release(version: str, cycle: str) -> sql.Release:
    return sql.Release(
        key=f"example-{version}",
        phase=sql.ReleasePhase.RELEASE,
        project_key="example",
        cycle_key=f"example-{cycle}",
        version=version,
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )


async def _release_row(data: db.Session, version: str) -> sql.Release:
    return await data.release(key=f"example-{version}").demand(AssertionError(f"Missing release {version}"))


async def _stored(data: db.Session, cycle: str) -> dict:
    overrides = await data.cycle_overrides(project_key="example", cycle=cycle).demand(AssertionError("Missing"))
    return overrides.settings


async def _sync(data: db.Session, given: dict[str, dict]) -> list[str]:
    writer = policy_writer.FoundationAdmin(mock.MagicMock(), mock.MagicMock(), data)
    changed = await writer.cycle_overrides_sync_no_commit(
        _PROJECT,
        {cycle: api.ProjectConfigCycleArgs.model_validate(settings) for cycle, settings in given.items()},
        asf_uid="system",
        update_type=sql.UpdateType.ASFYAML,
    )
    await data.commit()
    return changed


def _vote_form(
    cycle: str,
    *,
    start_vote_template: str = _VOTE_TEMPLATE,
    min_hours: int = 96,
    email_to: str = "dev@alpha.apache.org",
) -> shared_projects.VotePolicyForm:
    return shared_projects.VotePolicyForm(
        csrf_token="test",
        project_key=_PROJECT,
        cycle=cycle,
        email_to=email_to,
        min_hours=min_hours,
        release_checklist="",
        vote_comment_template="",
        start_vote_subject="",
        start_vote_template=start_vote_template,
        finish_vote_template="",
    )


def _writer(data: db.Session, committee_key: str = "alpha") -> policy_writer.ReleaseManager:
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    return policy_writer.ReleaseManager(write, mock.MagicMock(), data, committee_key)
