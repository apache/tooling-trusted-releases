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

from typing import Final

import pytest

import atr.models.validation as validation

REDOS_PATTERN: Final[str] = "^(a+)+$"


def test_match_ignore_pattern_avoids_redos_regression() -> None:
    value = ("a" * 4096) + "X"
    regex = validation.compile_ignore_pattern(REDOS_PATTERN)
    assert regex.search(value) is None


def test_validate_ignore_pattern_allows_literal_lookaround_tokens() -> None:
    validation.validate_ignore_pattern("(?=a)")


def test_validate_ignore_pattern_re2_supported_constructs() -> None:
    pattern = r"^(?i)apple(?-i)banana[[:digit:]]{2}\b|^cherry\s+date$"
    regex = validation.compile_ignore_pattern(pattern)
    assert regex.search("APPLEbanana12 ") is not None
    assert regex.search("applebanana99-") is not None
    assert regex.search("cherry   date") is not None
    assert regex.search("cherry\tdate") is not None

    assert regex.search("APPLEBANANA12 ") is None
    assert regex.search("applebanana123 ") is None
    assert regex.search("applebanana12x") is None
    assert regex.search("applebanana12_") is None
    assert regex.search("cherrydate") is None
    assert regex.search("xcherry   date") is None
    assert regex.search("cherry   datex") is None


def test_validate_ignore_pattern_rejects_regex_lookaround() -> None:
    with pytest.raises(ValueError, match="Invalid ignore pattern"):
        validation.validate_ignore_pattern("^(?=a)$")


def test_validate_ignore_pattern_rejects_too_long() -> None:
    pattern = "a" * (validation.MAX_IGNORE_PATTERN_LENGTH + 1)
    with pytest.raises(ValueError, match="Pattern exceeds"):
        validation.validate_ignore_pattern(pattern)
