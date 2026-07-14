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

import atr.calver as calver
import atr.cycles as cycles
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


def test_cycle_name_supports_named_cycle_via_capture():
    project = SimpleNamespace(key="example", cycle_match=r"^(\w+)-\d+\.\d+$")
    assert cycles.cycle_name_for_version(project, "stable-2.5") == "stable"


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
    candidates = [_release("1.1.0", _ts(1)), _release("1.2", _ts(2))]
    assert cycles.latest_release_in_cycle(project, "1.2", candidates) is None


def test_latest_release_falls_back_to_dates_when_the_whole_cycle_is_unrankable():
    # A semver project whose versions are all two-part: none parse, so they order by date
    # together, and exactly one of them must still come out as the latest
    project = SimpleNamespace(key="example", cycle_match=r"^(\d+)\..+", version_method=sql.VersionMethod.SEMVER)
    older, newer = _release("1.3", _ts(1)), _release("1.2", _ts(2))
    latest = cycles.latest_release_in_cycle(project, "1.3", [older, newer])
    assert latest is not None
    assert latest.version == "1.2"


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


def _release(version: str, released: datetime.datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(version=version, released=released)


def _ts(day: int) -> datetime.datetime:
    return datetime.datetime(2026, 1, day, 0, 0, 0, tzinfo=datetime.UTC)
