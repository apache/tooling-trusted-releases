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

import atr.datasources.apache as apache
import atr.models.sql as sql


def test_purge_handles_empty_release_managers() -> None:
    committee = _committee(members=["chair"], release_managers=[])

    apache._remove_member_release_managers(committee)

    assert committee.release_managers == []


def test_purge_keeps_non_member_release_managers() -> None:
    committee = _committee(members=["chair"], release_managers=["bob", "carol"])

    apache._remove_member_release_managers(committee)

    assert committee.release_managers == ["bob", "carol"]


def test_purge_removes_members_from_release_managers() -> None:
    committee = _committee(members=["chair", "carol"], release_managers=["bob", "carol"])

    apache._remove_member_release_managers(committee)

    assert committee.release_managers == ["bob"]


def _committee(members: list[str], release_managers: list[str]) -> sql.Committee:
    return sql.Committee(
        key="alpha",
        committee_members=members,
        committers=members + release_managers,
        release_managers=release_managers,
    )
