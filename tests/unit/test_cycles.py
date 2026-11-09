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
from types import SimpleNamespace

import pytest

import atr.cycles as cycles
import atr.models.calver as calver
import atr.models.sql as sql


def test_cycle_name_extracts_capture_group_for_matching_version():
    project = SimpleNamespace(key="example", cycle_match=r"^(\d+)\.\d+\.\d+$")
    assert cycles.cycle_name_for_version(project, "2.5.3") == "2"


def test_cycle_name_raises_when_capture_group_is_empty():
    project = SimpleNamespace(key="example", cycle_match=r"^(\d*)\.\d+$")
    with pytest.raises(ValueError, match="captured empty string"):
        cycles.cycle_name_for_version(project, ".5")


def test_cycle_name_raises_when_pattern_has_no_capture_groups():
    project = SimpleNamespace(key="example", cycle_match=r"^\d+\.\d+\.\d+$")
    with pytest.raises(ValueError, match="no capture groups"):
        cycles.cycle_name_for_version(project, "1.0.0")


def test_cycle_name_raises_when_version_does_not_match():
    project = SimpleNamespace(key="example", cycle_match=r"^(\d+)\.\d+\.\d+$")
    with pytest.raises(ValueError, match="does not match"):
        cycles.cycle_name_for_version(project, "garbage")


def test_cycle_name_returns_default_when_cycle_match_unset():
    project = SimpleNamespace(key="example", cycle_match=None)
    assert cycles.cycle_name_for_version(project, "1.0.0") == "default"


async def test_ensure_project_cycle_adds_missing_cycle_row():
    project = SimpleNamespace(key="example", cycle_match=r"(\d+)\..*")
    data = _CycleSession(existing=set())
    assert await cycles.ensure_project_cycle(data, project, "1.7.8") == "example-1"
    assert [(c.cycle_key, c.cycle) for c in data.added] == [("example-1", "1")]


async def test_ensure_project_cycle_raises_when_version_does_not_match():
    project = SimpleNamespace(key="example", cycle_match=r"(\d+)\..*")
    data = _CycleSession(existing=set())
    with pytest.raises(ValueError, match="does not match"):
        await cycles.ensure_project_cycle(data, project, "garbage")
    assert data.added == []


async def test_ensure_project_cycle_reuses_existing_cycle_row():
    project = SimpleNamespace(key="example", cycle_match=r"(\d+)\..*")
    data = _CycleSession(existing={"example-8"})
    assert await cycles.ensure_project_cycle(data, project, "8.2.0") == "example-8"
    assert data.added == []


def test_cycle_name_supports_named_cycle_via_capture():
    project = SimpleNamespace(key="example", cycle_match=r"^(\w+)-\d+\.\d+$")
    assert cycles.cycle_name_for_version(project, "stable-2.5") == "stable"


def test_newest_first_orders_cycles_by_number_with_default_last():
    names = ["10", "3", "default", "9", "11", "4"]
    ordered = cycles.newest_first(SimpleNamespace(cycle=name) for name in names)
    assert [c.cycle for c in ordered] == ["11", "10", "9", "4", "3", "default"]


def test_newest_first_orders_dotted_cycles_part_by_part():
    ordered = cycles.newest_first(SimpleNamespace(cycle=name) for name in ["2.9", "2.10", "10", "2"])
    assert [c.cycle for c in ordered] == ["10", "2.10", "2.9", "2"]


def test_prior_release_calver_picks_highest_below_target_within_a_cycle():
    # A cycled calver project: the YY.MM is the cycle, the trailing serial
    # the patch level. The prior of a patch is the patch below it in the line.
    project = SimpleNamespace(
        key="example",
        cycle_match=calver.cycle_regex("(YY.MM).N"),
        calver_format="(YY.MM).N",
        version_method=sql.VersionMethod.CALVER,
    )
    candidates = [
        _release("09.04", _ts(1)),
        _release("09.04.01", _ts(2)),
        _release("09.04.02", _ts(3)),
        _release("10.04", _ts(4)),
    ]
    prior = cycles.prior_release_in_cycle(project, "09.04.03", candidates)
    assert prior is not None
    assert prior.version == "09.04.02"


def test_prior_release_calver_orders_non_padded_dotted_dates():
    # A linear calver project with no cycle span: every release shares the
    # default cycle and ordering is purely by date.
    project = SimpleNamespace(
        key="example",
        cycle_match=None,
        calver_format="YYYY.M.D",
        version_method=sql.VersionMethod.CALVER,
    )
    # Released-date ordering deliberately reversed to prove we sort by version.
    candidates = [
        _release("2020.10.5", _ts(1)),
        _release("2020.6.24", _ts(10)),
        _release("2020.11.13", _ts(5)),
    ]
    prior = cycles.prior_release_in_cycle(project, "2020.12.1", candidates)
    assert prior is not None
    assert prior.version == "2020.11.13"


def test_prior_release_calver_falls_back_to_released_without_a_mask():
    project = SimpleNamespace(
        key="example",
        cycle_match=None,
        calver_format=None,
        version_method=sql.VersionMethod.CALVER,
    )
    candidates = [_release("2025.1", _ts(1)), _release("2025.2", _ts(5))]
    prior = cycles.prior_release_in_cycle(project, "2025.3", candidates)
    assert prior is not None
    assert prior.version == "2025.2"


def test_prior_release_calver_returns_none_when_target_does_not_fit_mask():
    project = SimpleNamespace(
        key="example",
        cycle_match=None,
        calver_format="YYYY.MM.DD",
        version_method=sql.VersionMethod.CALVER,
    )
    candidates = [_release("2025.01.01", _ts(1))]
    assert cycles.prior_release_in_cycle(project, "garbage", candidates) is None


def test_prior_release_filters_to_same_cycle_for_semver():
    # Major version cycle, candidate in 1.x must not be picked when target is 2.x
    project = SimpleNamespace(
        key="example",
        cycle_match=r"^(\d+)\.\d+\.\d+$",
        version_method=sql.VersionMethod.SEMVER,
    )
    candidates = [
        _release("1.9.0", _ts(1)),
        _release("2.0.0", _ts(5)),
        _release("2.0.1", _ts(10)),
    ]
    prior = cycles.prior_release_in_cycle(project, "2.1.0", candidates)
    assert prior is not None
    assert prior.version == "2.0.1"


def test_prior_release_returns_none_when_target_version_invalid_for_cycle():
    project = SimpleNamespace(
        key="example",
        cycle_match=r"^(\d+)\.\d+\.\d+$",
        version_method=sql.VersionMethod.SEMVER,
    )
    candidates = [_release("1.0.0", _ts(1))]
    assert cycles.prior_release_in_cycle(project, "garbage", candidates) is None


def test_prior_release_semver_excludes_versions_above_target():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SEMVER)
    candidates = [_release("1.0.0", _ts(1)), _release("2.0.0", _ts(5))]
    prior = cycles.prior_release_in_cycle(project, "1.5.0", candidates)
    assert prior is not None
    assert prior.version == "1.0.0"


def test_prior_release_semver_excludes_versions_equal_to_target():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SEMVER)
    candidates = [_release("1.0.0", _ts(1)), _release("1.2.0", _ts(5))]
    prior = cycles.prior_release_in_cycle(project, "1.2.0", candidates)
    assert prior is not None
    assert prior.version == "1.0.0"


def test_prior_release_semver_picks_highest_strictly_below_target():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SEMVER)
    # Released-date ordering deliberately reversed to prove we sort by version.
    candidates = [
        _release("1.2.0", _ts(1)),
        _release("1.0.1", _ts(10)),
        _release("1.1.0", _ts(5)),
    ]
    prior = cycles.prior_release_in_cycle(project, "1.3.0", candidates)
    assert prior is not None
    assert prior.version == "1.2.0"


def test_prior_release_semver_skips_unparsable_versions():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SEMVER)
    candidates = [_release("not-a-semver", _ts(1)), _release("1.0.0", _ts(5))]
    prior = cycles.prior_release_in_cycle(project, "1.1.0", candidates)
    assert prior is not None
    assert prior.version == "1.0.0"


def test_prior_release_semver_strips_v_prefix():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SEMVER)
    candidates = [_release("v1.0.0", _ts(1)), _release("v1.1.0", _ts(5))]
    prior = cycles.prior_release_in_cycle(project, "v1.2.0", candidates)
    assert prior is not None
    assert prior.version == "v1.1.0"


def test_prior_release_semver_ranks_two_part_versions():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SEMVER)
    # Released-date ordering deliberately reversed to prove we sort by version.
    candidates = [_release("4.0", _ts(10)), _release("4.1", _ts(5)), _release("3.9", _ts(1))]
    prior = cycles.prior_release_in_cycle(project, "4.2", candidates)
    assert prior is not None
    assert prior.version == "4.1"


def test_latest_release_semver_ranks_two_part_versions():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SEMVER)
    # 4.10 is later than 4.9 though it sorts before it as text.
    candidates = [_release("4.9", _ts(10)), _release("4.10", _ts(1)), _release("4.2", _ts(5))]
    latest = cycles.latest_release_in_cycle(project, "4.9", candidates)
    assert latest is not None
    assert latest.version == "4.10"


def test_semver_parse_reads_missing_parts_as_zero():
    assert str(cycles.semver_parse("3.0")) == "3.0.0"
    assert str(cycles.semver_parse("v3")) == "3.0.0"
    assert str(cycles.semver_parse("3.0-rc1")) == "3.0.0-rc1"


def test_semver_parse_rejects_what_is_not_semver():
    assert cycles.semver_parse("1.2.3.4") is None
    assert cycles.semver_parse("0.05") is None
    assert cycles.semver_parse("garbage") is None


def test_latest_release_calver_picks_highest_within_the_target_cycle():
    project = SimpleNamespace(
        key="example",
        cycle_match=calver.cycle_regex("(YY.MM).N"),
        calver_format="(YY.MM).N",
        version_method=sql.VersionMethod.CALVER,
    )
    candidates = [
        _release("09.04", _ts(1)),
        _release("09.04.02", _ts(3)),
        _release("09.04.01", _ts(2)),
        _release("10.04", _ts(4)),
    ]
    latest = cycles.latest_release_in_cycle(project, "09.04", candidates)
    assert latest is not None
    assert latest.version == "09.04.02"


def test_latest_release_returns_none_when_nothing_is_rankable():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SIMPLE)
    candidates = [_release("1.0.0", None), _release("1.1.0", None)]
    assert cycles.latest_release_in_cycle(project, "1.0.0", candidates) is None


def test_latest_release_returns_none_when_the_target_alone_cannot_be_ranked():
    # Ranking it against siblings it can't be compared with would make it look superseded
    project = SimpleNamespace(key="example", cycle_match=r"^(\d+)\..+", version_method=sql.VersionMethod.SEMVER)
    candidates = [_release("1.1.0", _ts(1)), _release("1.2.0.1", _ts(2))]
    assert cycles.latest_release_in_cycle(project, "1.2.0.1", candidates) is None


def test_latest_release_falls_back_to_dates_when_the_whole_cycle_is_unrankable():
    # A semver project whose versions are all four-part: none parse, so they order by date
    # together, and exactly one of them must still come out as the latest
    project = SimpleNamespace(key="example", cycle_match=r"^(\d+)\..+", version_method=sql.VersionMethod.SEMVER)
    older, newer = _release("1.3.0.1", _ts(1)), _release("1.2.0.1", _ts(2))
    latest = cycles.latest_release_in_cycle(project, "1.3.0.1", [older, newer])
    assert latest is not None
    assert latest.version == "1.2.0.1"


def test_latest_release_undated_target_cannot_be_ranked_by_date():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SIMPLE)
    candidates = [_release("1.0.0", _ts(1)), _release("1.1.0", None)]
    assert cycles.latest_release_in_cycle(project, "1.1.0", candidates) is None


def test_latest_release_semver_ignores_release_dates():
    # Released out of order; the version scheme decides, not the dates
    project = SimpleNamespace(key="example", cycle_match=r"^(\d+)\.\d+\.\d+$", version_method=sql.VersionMethod.SEMVER)
    candidates = [
        _release("1.0.5", _ts(9)),
        _release("1.1.0", _ts(5)),
        _release("2.0.0", _ts(1)),
    ]
    latest = cycles.latest_release_in_cycle(project, "1.0.0", candidates)
    assert latest is not None
    assert latest.version == "1.1.0"


def test_latest_release_simple_returns_most_recently_released():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SIMPLE)
    candidates = [
        _release("1.0.0", _ts(1)),
        _release("1.1.0", _ts(5)),
        _release("1.0.1", _ts(3)),
    ]
    latest = cycles.latest_release_in_cycle(project, "1.0.0", candidates)
    assert latest is not None
    assert latest.version == "1.1.0"


def test_prior_release_simple_returns_most_recently_released():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SIMPLE)
    candidates = [
        _release("1.0.0", _ts(1)),
        _release("1.1.0", _ts(5)),
        _release("1.0.1", _ts(3)),
    ]
    prior = cycles.prior_release_in_cycle(project, "1.2.0", candidates)
    assert prior is not None
    assert prior.version == "1.1.0"


def test_prior_release_simple_returns_none_when_no_candidates():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SIMPLE)
    assert cycles.prior_release_in_cycle(project, "1.0.0", []) is None


def test_prior_release_simple_skips_candidates_without_released_date():
    project = SimpleNamespace(key="example", cycle_match=None, version_method=sql.VersionMethod.SIMPLE)
    candidates = [_release("1.0.0", None), _release("1.1.0", None)]
    assert cycles.prior_release_in_cycle(project, "1.2.0", candidates) is None


class _CycleQuery:
    def __init__(self, found: bool) -> None:
        self._found = found

    async def get(self) -> object | None:
        return object() if self._found else None


class _CycleSession:
    def __init__(self, existing: set[str]) -> None:
        self._existing = existing
        self.added: list[sql.ProjectCycle] = []

    def add(self, row: sql.ProjectCycle) -> None:
        self.added.append(row)

    def project_cycle(self, cycle_key: str) -> _CycleQuery:
        return _CycleQuery(cycle_key in self._existing)


def _release(version: str, released: datetime.datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(version=version, released=released)


def _ts(day: int) -> datetime.datetime:
    return datetime.datetime(2026, 1, day, 0, 0, 0, tzinfo=datetime.UTC)
