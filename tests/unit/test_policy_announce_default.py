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


def test_policy_announce_release_default_includes_disclaimer_for_podlings() -> None:
    project = sql.Project(key="p", status=sql.ProjectStatus.ACTIVE)
    project.committee = sql.Committee(key="p", is_podling=True)
    assert "{{PODLING_DISCLAIMER}}" in project.policy_announce_release_default


def test_policy_announce_release_default_omits_disclaimer_for_non_podlings() -> None:
    project = sql.Project(key="p", status=sql.ProjectStatus.ACTIVE)
    project.committee = sql.Committee(key="p", is_podling=False)
    template = project.policy_announce_release_default
    assert "{{PODLING_DISCLAIMER}}" not in template
    assert "{{DOWNLOAD_URL}}\n\nOn behalf" in template
