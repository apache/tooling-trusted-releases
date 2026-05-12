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

import atr.models.sql as sql
import atr.shared.ignores as ignores


def test_cross_enum_value_equality_for_ignore_match() -> None:
    assert sql.CheckResultStatus.CONCERN.value == sql.CheckResultStatusIgnore.CONCERN.value
    assert sql.CheckResultStatus.SUGGESTION.value == sql.CheckResultStatusIgnore.SUGGESTION.value
    assert sql.CheckResultStatus.EXCEPTION.value == sql.CheckResultStatusIgnore.EXCEPTION.value


def test_ignore_status_enum_members() -> None:
    assert sql.CheckResultStatusIgnore.CONCERN.value == "concern"
    assert sql.CheckResultStatusIgnore.SUGGESTION.value == "suggestion"
    assert sql.CheckResultStatusIgnore.EXCEPTION.value == "exception"


def test_ignore_status_form_field_roundtrip() -> None:
    for member in sql.CheckResultStatusIgnore:
        assert sql.CheckResultStatusIgnore.from_form_field(member.to_form_field()) is member


def test_ignore_status_wrapper_roundtrip() -> None:
    for member in ignores.IgnoreStatus:
        if member is ignores.IgnoreStatus.NO_STATUS:
            assert ignores.ignore_status_to_sql(member) is None
            continue
        sql_value = ignores.ignore_status_to_sql(member)
        assert sql_value is not None
        assert ignores.sql_to_ignore_status(sql_value) is member


def test_status_enum_members() -> None:
    assert sql.CheckResultStatus.NOTE.value == "note"
    assert sql.CheckResultStatus.SUGGESTION.value == "suggestion"
    assert sql.CheckResultStatus.CONCERN.value == "concern"
    assert sql.CheckResultStatus.BLOCKER.value == "blocker"
    assert sql.CheckResultStatus.EXCEPTION.value == "exception"
