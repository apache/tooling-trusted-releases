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

import re

import pytest

import atr.calver as calver


def test_order_key_compares_dotted_non_padded_dates_numerically():
    # Compared as strings "2020.10.5" sorts below "2020.6.24" ("1" < "6"), so
    # the fields have to be compared as numbers instead.
    date_format = "YYYY.M.D"
    later = calver.order_key(date_format, "2020.10.5")
    earlier = calver.order_key(date_format, "2020.6.24")
    assert later is not None
    assert earlier is not None
    assert later > earlier


def test_order_key_handles_run_together_mixed_granularity():
    # A six- and an eight-digit version share one format; the shorter one reads
    # its missing day as 0 rather than misparsing.
    date_format = "YYYYMMDD"
    assert calver.order_key(date_format, "201407") == (2014, 7, 0, 0)
    assert calver.order_key(date_format, "20130710") == (2013, 7, 10, 0)
    # The earlier year sorts first despite being the larger raw integer.
    earlier = calver.order_key(date_format, "20130710")
    later = calver.order_key(date_format, "201407")
    assert earlier is not None
    assert later is not None
    assert earlier < later


def test_order_key_reads_dashed_date():
    assert calver.order_key("YYYY-MM-DD", "2025-11-03") == (2025, 11, 3, 0)


def test_order_key_places_serial_after_calendar_fields():
    date_format = "(YY.MM).N"
    assert calver.order_key(date_format, "09.04") == (9, 4, 0, 0)
    assert calver.order_key(date_format, "09.04.01") == (9, 4, 0, 1)
    first_patch = calver.order_key(date_format, "09.04.01")
    second_patch = calver.order_key(date_format, "09.04.02")
    assert first_patch is not None
    assert second_patch is not None
    assert first_patch < second_patch


def test_order_key_returns_none_for_unparseable_version():
    assert calver.order_key("YYYY.MM.DD", "not-a-date") is None


def test_cycle_regex_captures_parenthesised_span_as_group_one():
    pattern = calver.cycle_regex("(YY.MM).N")
    assert pattern is not None
    compiled = re.compile(pattern)
    patched = compiled.fullmatch("09.04.01")
    assert patched is not None
    assert patched.group(1) == "09.04"
    # The trailing serial is optional, so the bare line still matches.
    bare = compiled.fullmatch("09.04")
    assert bare is not None
    assert bare.group(1) == "09.04"


def test_cycle_regex_returns_none_for_linear_format():
    # No parenthesised span means a single default cycle, so cycle_match stays unset.
    assert calver.cycle_regex("YYYY.MM.DD") is None


def test_cycle_regex_round_trips_through_fullmatch():
    pattern = calver.cycle_regex("(YYYY)-MM-DD")
    assert pattern is not None
    matched = re.compile(pattern).fullmatch("2025-11-03")
    assert matched is not None
    assert matched.group(1) == "2025"


def test_validate_rejects_format_without_calendar_field():
    with pytest.raises(ValueError, match="at least one"):
        calver.validate("N.N")


def test_validate_rejects_repeated_field():
    with pytest.raises(ValueError, match="repeats"):
        calver.validate("YYYY.YYYY")


def test_validate_rejects_unbalanced_parentheses():
    with pytest.raises(ValueError, match="unmatched"):
        calver.validate("(YY.MM")


def test_validate_rejects_two_cycle_spans():
    with pytest.raises(ValueError, match="at most one"):
        calver.validate("(YY).(MM)")


def test_validate_accepts_real_world_formats():
    for date_format in ("YYYY.M.D", "YYYYMMDD", "YYYY-MM-DD", "(YY.MM).N"):
        calver.validate(date_format)
