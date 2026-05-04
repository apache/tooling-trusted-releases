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

from types import SimpleNamespace

import pytest

import atr.cycles as cycles


def test_cycle_name_returns_default_when_cycle_match_unset():
    project = SimpleNamespace(key="example", cycle_match=None)
    assert cycles.cycle_name_for_version(project, "1.0.0") == "default"


def test_cycle_name_extracts_capture_group_for_matching_version():
    project = SimpleNamespace(key="example", cycle_match=r"^(\d+)\.\d+\.\d+$")
    assert cycles.cycle_name_for_version(project, "2.5.3") == "2"


def test_cycle_name_supports_named_cycle_via_capture():
    project = SimpleNamespace(key="example", cycle_match=r"^(\w+)-\d+\.\d+$")
    assert cycles.cycle_name_for_version(project, "stable-2.5") == "stable"


def test_cycle_name_raises_when_version_does_not_match():
    project = SimpleNamespace(key="example", cycle_match=r"^(\d+)\.\d+\.\d+$")
    with pytest.raises(ValueError, match="does not match"):
        cycles.cycle_name_for_version(project, "garbage")


def test_cycle_name_raises_when_capture_group_is_empty():
    project = SimpleNamespace(key="example", cycle_match=r"^(\d*)\.\d+$")
    with pytest.raises(ValueError, match="captured empty string"):
        cycles.cycle_name_for_version(project, ".5")


def test_cycle_name_raises_when_pattern_has_no_capture_groups():
    project = SimpleNamespace(key="example", cycle_match=r"^\d+\.\d+\.\d+$")
    with pytest.raises(ValueError, match="no capture groups"):
        cycles.cycle_name_for_version(project, "1.0.0")
