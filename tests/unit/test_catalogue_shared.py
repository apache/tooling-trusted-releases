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
import atr.shared.repoint as repoint


def test_registries_include_every_key_referencing_table() -> None:
    # Every table that foreign-keys a project, release, or cycle key must appear,
    # so the re-point engine never orphans a row
    project_models = {model for model, _ in repoint.PROJECT_KEY_REFS}
    release_models = {model for model, _ in repoint.RELEASE_KEY_REFS}
    assert sql.Artifact in project_models
    assert sql.LifecycleEvent in project_models
    assert sql.Task in project_models
    assert sql.CheckResultIgnore in project_models
    assert sql.Distribution in release_models
    assert sql.BallotPaper in release_models
    assert sql.Revision in release_models
