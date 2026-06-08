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

import collections
from types import SimpleNamespace

import atr.models.sql as sql
import atr.storage.datatypes as datatypes
import atr.util as util


def test_concern_groups_and_missing():
    headers_stat = datatypes.CheckerStats(
        checker="atr.tasks.checks.license.headers",
        counts=collections.Counter(
            {
                sql.CheckResultStatus.CONCERN: 6,
                sql.CheckResultStatus.SUGGESTION: 4,
            }
        ),
        files={sql.CheckResultStatus.CONCERN: {"a": 6}},
    )
    paths_stat = datatypes.CheckerStats(
        checker="atr.tasks.checks.paths",
        counts=collections.Counter({sql.CheckResultStatus.CONCERN: 3}),
        files={sql.CheckResultStatus.CONCERN: {"a": 3}},
    )
    suggestions_only_stat = datatypes.CheckerStats(
        checker="atr.tasks.checks.targz.structure",
        counts=collections.Counter({sql.CheckResultStatus.SUGGESTION: 2}),
        files={sql.CheckResultStatus.SUGGESTION: {"a": 2}},
    )
    release_level = SimpleNamespace(checker="atr.tasks.checks.signature")
    info = datatypes.PathInfo.model_construct(
        checker_stats=[headers_stat, paths_stat, suggestions_only_stat],
        release_level_concerns=[release_level],
    )

    groups = util.concern_groups(info)

    assert [(group.checker, group.label, group.count) for group in groups] == [
        ("atr.tasks.checks.license.headers", "License Headers", 6),
        ("atr.tasks.checks.paths", "Paths", 3),
        ("atr.tasks.checks.signature", "Signature", 1),
    ]
    assert util.missing_concern_groups(groups, [group.checker for group in groups]) == []
    assert util.missing_concern_groups(groups, [groups[0].checker]) == [groups[1], groups[2]]
    assert util.missing_concern_groups(groups, ["unknown.checker"]) == groups
