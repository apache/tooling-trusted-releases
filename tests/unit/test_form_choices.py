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

import enum

import atr.form as form


class _Colour(enum.Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class _ColourForm(form.Form):
    colour: form.Enum[_Colour] = form.label("Colour", widget=form.Widget.SELECT)


class _StaticallyFilteredColourForm(form.Form):
    colour: form.Enum[_Colour] = form.label("Colour", widget=form.Widget.SELECT, enum_filter_include=["green"])


def test_get_choices_applies_render_time_filter() -> None:
    field_info = _ColourForm.model_fields["colour"]
    assert form._get_choices(field_info, filter_keys=["red", "blue"]) == [("red", "red"), ("blue", "blue")]


def test_get_choices_honours_static_enum_filter_include() -> None:
    field_info = _StaticallyFilteredColourForm.model_fields["colour"]
    assert form._get_choices(field_info) == [("green", "green")]


def test_get_choices_returns_all_members_by_default() -> None:
    field_info = _ColourForm.model_fields["colour"]
    assert form._get_choices(field_info) == [("red", "red"), ("green", "green"), ("blue", "blue")]


def test_render_time_filter_overrides_static_filter() -> None:
    field_info = _StaticallyFilteredColourForm.model_fields["colour"]
    assert form._get_choices(field_info, filter_keys=["red"]) == [("red", "red")]
