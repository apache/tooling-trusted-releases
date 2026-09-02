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

import pytest

import atr.storage.writers.keys as keys
import atr.svn.keys_reflect as keys_reflect


def _plan(
    present: set[str], linked: set[str], in_use: set[str], flagged: set[str] | None = None
) -> keys._ReflectionPlan:
    return keys._reflection_plan(
        present=frozenset(present),
        linked=frozenset(linked),
        in_use=frozenset(in_use),
        flagged=frozenset(flagged or set()),
    )


def test_keys_present_in_both_are_left_untouched():
    plan = _plan(present={"a"}, linked={"a"}, in_use=set())
    assert plan.to_remove == frozenset()
    assert plan.to_flag == frozenset()
    assert plan.to_clear == frozenset()


def test_key_absent_from_svn_and_unused_is_removed():
    plan = _plan(present=set(), linked={"a"}, in_use=set())
    assert plan.to_remove == frozenset({"a"})
    assert plan.to_flag == frozenset()


def test_key_absent_from_svn_but_in_use_is_flagged_not_removed():
    plan = _plan(present=set(), linked={"a"}, in_use={"a"})
    assert plan.to_remove == frozenset()
    assert plan.to_flag == frozenset({"a"})


def test_in_use_key_already_flagged_is_not_flagged_again():
    plan = _plan(present=set(), linked={"a"}, in_use={"a"}, flagged={"a"})
    assert plan.to_flag == frozenset()
    assert plan.to_remove == frozenset()


def test_flag_is_cleared_when_a_key_returns_to_svn():
    plan = _plan(present={"a"}, linked={"a"}, in_use={"a"}, flagged={"a"})
    assert plan.to_clear == frozenset({"a"})
    assert plan.to_flag == frozenset()
    assert plan.to_remove == frozenset()


def test_new_key_only_in_svn_does_not_appear_in_any_removal_set():
    plan = _plan(present={"a", "b"}, linked={"a"}, in_use=set())
    assert plan.to_remove == frozenset()
    assert plan.to_flag == frozenset()
    assert plan.to_clear == frozenset()


def test_tlp_keys_change_names_the_committee():
    changed = {"release/httpd/KEYS": {"flags": "U"}}
    assert keys_reflect._committees_with_keys_change(changed) == {"httpd"}


def test_podling_keys_change_names_the_podling():
    changed = {"release/incubator/somepodling/KEYS": {"flags": "A"}}
    assert keys_reflect._committees_with_keys_change(changed) == {"somepodling"}


def test_deleted_keys_file_is_not_reflected():
    changed = {"release/httpd/KEYS": {"flags": "D"}}
    assert keys_reflect._committees_with_keys_change(changed) == set()


def test_non_keys_and_subproject_keys_paths_are_ignored():
    changed = {
        "release/httpd/httpd-2.4.1.tar.gz": {"flags": "A"},
        "release/httpd/subproject/KEYS": {"flags": "A"},
        "dev/httpd/KEYS": {"flags": "A"},
    }
    assert keys_reflect._committees_with_keys_change(changed) == set()


async def test_export_keys_refuses_a_file_over_the_byte_limit(monkeypatch, tmp_path) -> None:
    written = {}

    async def export(_url: str, _revision: int | None, destination) -> None:
        destination.write_bytes(written["data"])

    monkeypatch.setattr(keys_reflect.constants, "KEYS_FILE_LIMIT_BYTES", 8)
    monkeypatch.setattr(keys_reflect.paths, "get_tmp_dir", lambda: tmp_path)
    monkeypatch.setattr(keys_reflect.svn, "export", export)

    written["data"] = b"x\r\nx" * 2
    assert await keys_reflect._export_keys("svn://dist/release/alpha/KEYS", None) == "x\nx" * 2
    written["data"] = b"x" * 9
    with pytest.raises(ValueError, match="9 bytes, larger than 8"):
        await keys_reflect._export_keys("svn://dist/release/alpha/KEYS", None)
