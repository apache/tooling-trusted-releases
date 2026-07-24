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
import re
import unittest.mock as mock
from types import SimpleNamespace

import pydantic
import pytest

import atr.models.api as api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared.projects as shared_projects
import atr.storage as storage
import atr.storage.writers.policy as policy_writer


class _Query:
    def __init__(self, value):
        self._value = value

    async def get(self):
        return self._value

    async def demand(self, error):
        if self._value is None:
            raise error
        return self._value


class _MockData:
    def __init__(self, *, project=None, cycle=None, prior_event_id=None, releases=None, cycles_by_key=None):
        self._project = project
        self._cycle = cycle
        self._releases = releases or []
        self._cycles_by_key = cycles_by_key or {}
        # Older tests pass `cycle=...`; mirror it into the by-key index so the
        # writer's lookups via cycle_key still find it.
        if cycle is not None and getattr(cycle, "cycle_key", None) is not None:
            self._cycles_by_key.setdefault(cycle.cycle_key, cycle)
        self.added: list[object] = []
        self.commit = mock.AsyncMock()
        self.execute = mock.AsyncMock()
        result = mock.MagicMock()
        result.scalar_one_or_none.return_value = prior_event_id
        self.execute.return_value = result

    def add(self, item):
        self.added.append(item)
        # Track newly-added cycles so subsequent project_cycle lookups find them.
        if isinstance(item, sql.ProjectCycle):
            self._cycles_by_key[item.cycle_key] = item

    def project(self, **_kwargs):
        return _Query(self._project)

    def project_cycle(self, *, cycle_key=None, **_kwargs):
        if cycle_key is not None:
            return _Query(self._cycles_by_key.get(cycle_key))
        return _Query(self._cycle)

    def release(self, **_kwargs):
        # The writer calls .all() on this query path.
        all_mock = mock.AsyncMock(return_value=list(self._releases))
        return SimpleNamespace(all=all_mock)


async def test_edit_cycle_dates_pairs_withdraw_when_changing_existing_date():
    existing = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
    cycle = _cycle(eod=existing)
    data = _MockData(project=_project(), cycle=cycle, prior_event_id=42)
    writer = _make_committee_member(data)
    form = _cycle_dates_form(eod=datetime.date(2026, 7, 1))

    await writer.edit_cycle_dates(form)

    events = [e for e in data.added if isinstance(e, sql.LifecycleEvent)]
    assert len(events) == 2
    withdraw, new_event = events
    assert withdraw.event is sql.LifecycleEventType.WITHDRAW
    assert withdraw.target_event_id == 42
    assert new_event.event is sql.LifecycleEventType.EOD
    assert new_event.effective.date() == datetime.date(2026, 7, 1)


async def test_edit_cycle_dates_raises_when_cycle_missing():
    data = _MockData(project=_project(), cycle=None)
    writer = _make_committee_member(data)
    form = _cycle_dates_form(eod=datetime.date(2026, 6, 1))

    with pytest.raises(storage.AccessError, match="not found"):
        await writer.edit_cycle_dates(form)


async def test_edit_cycle_dates_rejects_cycle_belonging_to_other_project():
    cycle = _cycle(project_key="otherproject")
    data = _MockData(project=_project(), cycle=cycle)
    writer = _make_committee_member(data)
    form = _cycle_dates_form(project_key="example", eod=datetime.date(2026, 6, 1))

    with pytest.raises(storage.AccessError, match="does not belong to project"):
        await writer.edit_cycle_dates(form)


async def test_edit_cycle_dates_skips_unchanged_date():
    existing = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
    cycle = _cycle(eod=existing)
    data = _MockData(project=_project(), cycle=cycle)
    writer = _make_committee_member(data)
    form = _cycle_dates_form(eod=datetime.date(2026, 6, 1))

    await writer.edit_cycle_dates(form)

    events = [e for e in data.added if isinstance(e, sql.LifecycleEvent)]
    assert events == []
    data.execute.assert_not_awaited()


async def test_edit_cycle_dates_writes_event_when_setting_first_eod():
    cycle = _cycle()
    data = _MockData(project=_project(), cycle=cycle)
    writer = _make_committee_member(data)
    form = _cycle_dates_form(eod=datetime.date(2026, 6, 1))

    await writer.edit_cycle_dates(form)

    events = [e for e in data.added if isinstance(e, sql.LifecycleEvent)]
    assert len(events) == 1
    assert events[0].event is sql.LifecycleEventType.EOD
    assert events[0].cycle_key == "example-default"
    assert events[0].project_key == "example"
    assert events[0].effective.date() == datetime.date(2026, 6, 1)
    assert cycle.eod.date() == datetime.date(2026, 6, 1)
    data.commit.assert_awaited_once()


async def test_edit_version_scheme_moves_unmatched_releases_to_default():
    project = _project()
    releases = [_release("1.2.3"), _release("weird-version")]
    default_cycle = SimpleNamespace(cycle_key="example-default")
    data = _MockData(
        project=project,
        releases=releases,
        cycles_by_key={"example-default": default_cycle},
    )
    writer = _make_committee_member(data)
    form = _version_scheme_form(cycle_match=r"^(\d+)\.\d+\.\d+$")

    await writer.edit_version_scheme(form)

    assert releases[0].cycle_key == "example-1"
    assert releases[1].cycle_key == "example-default"


async def test_edit_version_scheme_normalises_empty_strings_to_none():
    project = _project(
        version_pattern=r"^\d+$",
        cycle_match=r"(\d+)",
        branch_template="release-{cycle}",
    )
    data = _MockData(project=project)
    writer = _make_committee_member(data)
    form = _version_scheme_form(version_pattern="", cycle_match="", branch_template="")

    await writer.edit_version_scheme(form)

    assert project.version_pattern is None
    assert project.cycle_match is None
    assert project.branch_template is None


async def test_edit_version_scheme_raises_when_project_missing():
    data = _MockData(project=None)
    writer = _make_committee_member(data)
    form = _version_scheme_form()

    with pytest.raises(storage.AccessError, match="not found"):
        await writer.edit_version_scheme(form)


async def test_edit_version_scheme_reassigns_releases_into_new_cycles():
    project = _project()
    releases = [_release("0.1"), _release("0.2"), _release("1.5")]
    default_cycle = SimpleNamespace(cycle_key="example-default")
    data = _MockData(
        project=project,
        releases=releases,
        cycles_by_key={"example-default": default_cycle},
    )
    writer = _make_committee_member(data)
    form = _version_scheme_form(cycle_match=r"^(\d+)\.\d+$")

    await writer.edit_version_scheme(form)

    assert releases[0].cycle_key == "example-0"
    assert releases[1].cycle_key == "example-0"
    assert releases[2].cycle_key == "example-1"
    new_cycles = [c for c in data.added if isinstance(c, sql.ProjectCycle)]
    assert {c.cycle_key for c in new_cycles} == {"example-0", "example-1"}


async def test_edit_version_scheme_rejects_cycle_match_without_capture_group():
    project = _project()
    data = _MockData(project=project)
    writer = _make_committee_member(data)

    with pytest.raises(pydantic.ValidationError, match="at least one capture group"):
        form = _version_scheme_form(cycle_match=r"^\d+\.\d+\.\d+$")
        await writer.edit_version_scheme(form)


async def test_edit_version_scheme_rejects_invalid_cycle_match_regex():
    project = _project()
    data = _MockData(project=project)
    writer = _make_committee_member(data)

    with pytest.raises(pydantic.ValidationError, match="Cycle match is not a valid regex"):
        form = _version_scheme_form(cycle_match="(unclosed")
        await writer.edit_version_scheme(form)


async def test_edit_version_scheme_rejects_invalid_version_pattern_regex():
    project = _project()
    data = _MockData(project=project)
    writer = _make_committee_member(data)

    with pytest.raises(pydantic.ValidationError, match="Version pattern is not a valid regex"):
        form = _version_scheme_form(version_pattern="(unclosed")
        await writer.edit_version_scheme(form)


async def test_edit_version_scheme_saves_all_fields_normalised():
    project = _project()
    data = _MockData(project=project)
    writer = _make_committee_member(data)
    form = _version_scheme_form(
        version_method=sql.VersionMethod.SEMVER,
        version_pattern=r"^\d+\.\d+\.\d+$",
        cycle_match=r"^(\d+)\.\d+\.\d+$",
        branch_template="release-{cycle}",
    )

    await writer.edit_version_scheme(form)

    assert project.version_method is sql.VersionMethod.SEMVER
    assert project.version_pattern == r"^\d+\.\d+\.\d+$"
    assert project.cycle_match == r"^(\d+)\.\d+\.\d+$"
    assert project.branch_template == "release-{cycle}"
    data.commit.assert_awaited_once()


async def test_edit_version_scheme_compiles_calver_mask_cycle_span_into_cycle_match():
    project = _project()
    data = _MockData(project=project)
    writer = _make_committee_member(data)
    form = _version_scheme_form(version_method=sql.VersionMethod.CALVER, calver_format="(YY.MM).N")

    await writer.edit_version_scheme(form)

    assert project.version_method is sql.VersionMethod.CALVER
    assert project.calver_format == "(YY.MM).N"
    # The parenthesised span becomes the cycle_match capture group.
    assert project.cycle_match is not None
    matched = re.compile(project.cycle_match).fullmatch("09.04.01")
    assert matched is not None
    assert matched.group(1) == "09.04"


async def test_edit_version_scheme_leaves_cycle_match_unset_for_linear_calver():
    project = _project()
    data = _MockData(project=project)
    writer = _make_committee_member(data)
    form = _version_scheme_form(version_method=sql.VersionMethod.CALVER, calver_format="YYYY.MM.DD")

    await writer.edit_version_scheme(form)

    assert project.calver_format == "YYYY.MM.DD"
    assert project.cycle_match is None


async def test_edit_version_scheme_rejects_invalid_calver_mask():
    project = _project()
    data = _MockData(project=project)
    writer = _make_committee_member(data)

    with pytest.raises(pydantic.ValidationError, match="at least one"):
        form = _version_scheme_form(version_method=sql.VersionMethod.CALVER, calver_format="N.N")
        await writer.edit_version_scheme(form)


async def test_edit_version_scheme_skips_release_already_in_correct_cycle():
    project = _project()
    releases = [_release("0.1", cycle_key="example-0")]
    cycle_zero = SimpleNamespace(cycle_key="example-0")
    data = _MockData(
        project=project,
        releases=releases,
        cycles_by_key={"example-0": cycle_zero, "example-default": SimpleNamespace(cycle_key="example-default")},
    )
    writer = _make_committee_member(data)
    form = _version_scheme_form(cycle_match=r"^(\d+)\.\d+$")

    await writer.edit_version_scheme(form)

    assert releases[0].cycle_key == "example-0"
    new_cycles = [c for c in data.added if isinstance(c, sql.ProjectCycle)]
    assert new_cycles == []


async def test_edit_policy_rejects_unknown_template_variables():
    project = SimpleNamespace(
        key="example",
        status=sql.ProjectStatus.ACTIVE,
        is_active=True,
        committee_key="alpha",
        committee=None,
        release_policy=SimpleNamespace(vote_mode=None, recipient_defaults={}),
        mark_updated=lambda **_kwargs: None,
    )
    data = _MockData(project=project)
    writer = _make_committee_member(data)
    good = api.PolicyUpdateArgs.model_validate(
        {"project": "example", "start_vote_template": "Open for {{DURATION}} hours."}
    )
    await writer.edit_policy(safe.ProjectKey("example"), good)

    bad = api.PolicyUpdateArgs.model_validate({"project": "example", "start_vote_template": "{{BAD}} {{WORSE}}"})
    with pytest.raises(ValueError, match="Unknown template variables: BAD, WORSE"):
        await writer.edit_policy(safe.ProjectKey("example"), bad)


def _cycle(project_key="example", cycle_key="example-default", eod=None, eos=None, eol=None, lts=False):
    return SimpleNamespace(
        project_key=project_key,
        cycle_key=cycle_key,
        cycle=cycle_key.removeprefix(f"{project_key}-"),
        eod=eod,
        eos=eos,
        eol=eol,
        lts=lts,
    )


def _cycle_dates_form(*, project_key="example", cycle_key="example-default", eod=None, eos=None, eol=None, lts=False):
    return shared_projects.EditCycleDatesForm(
        variant="edit_cycle_dates",
        csrf_token="test",
        project_key=safe.ProjectKey(project_key),
        cycle_key=cycle_key,
        eod=eod,
        eos=eos,
        eol=eol,
        lts=lts,
    )


def _make_committee_member(data):
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    write_as = mock.MagicMock()
    return policy_writer.CommitteeMember(write, write_as, data, "alpha")


def _project(
    key="example", version_method=sql.VersionMethod.SIMPLE, version_pattern=None, cycle_match=None, branch_template=None
):
    return SimpleNamespace(
        key=key,
        status=sql.ProjectStatus.ACTIVE,
        is_active=True,
        committee_key="alpha",
        version_method=version_method,
        version_pattern=version_pattern,
        cycle_match=cycle_match,
        branch_template=branch_template,
        mark_updated=lambda **_kwargs: None,
    )


def _release(version, project_key="example", cycle_key=None):
    return SimpleNamespace(
        version=version,
        project_key=project_key,
        cycle_key=cycle_key or f"{project_key}-default",
    )


def _version_scheme_form(
    *,
    project_key="example",
    version_method=sql.VersionMethod.SIMPLE,
    version_pattern="",
    cycle_match="",
    calver_format="",
    branch_template="",
):
    return shared_projects.EditVersionSchemeForm(
        variant="edit_version_scheme",
        csrf_token="test",
        project_key=safe.ProjectKey(project_key),
        version_method=version_method,
        version_pattern=version_pattern,
        cycle_match=cycle_match,
        calver_format=calver_format,
        branch_template=branch_template,
    )
