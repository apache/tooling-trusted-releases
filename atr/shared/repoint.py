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

import dataclasses
from typing import Final

import sqlmodel

import atr.models.sql as sql

# Tables whose column holds a project key verbatim
PROJECT_KEY_REFS: Final[list[tuple[type[sqlmodel.SQLModel], str]]] = [
    (sql.Project, "super_project_key"),
    (sql.ProjectCycle, "project_key"),
    (sql.Release, "project_key"),
    (sql.Artifact, "project_key"),
    (sql.LifecycleEvent, "project_key"),
    (sql.Task, "project_key"),
    (sql.CheckResultIgnore, "project_key"),
]

# Tables whose column holds a release key, "{project_key}-{version}". Task.version_key
# is a logical reference, not a foreign key, but we rewrite it too so a moved project
# leaves no stale check-task pointers
RELEASE_KEY_REFS: Final[list[tuple[type[sqlmodel.SQLModel], str]]] = [
    (sql.Release, "key"),
    (sql.Artifact, "release_key"),
    (sql.LifecycleEvent, "version_key"),
    (sql.CheckResult, "release_key"),
    (sql.BallotPaper, "release_key"),
    (sql.Distribution, "release_key"),
    (sql.Quarantined, "release_key"),
    (sql.Revision, "release_key"),
    (sql.Task, "version_key"),
]

# Tables whose column holds a cycle key, "{project_key}-{cycle}"
CYCLE_KEY_REFS: Final[list[tuple[type[sqlmodel.SQLModel], str]]] = [
    (sql.ProjectCycle, "cycle_key"),
    (sql.Release, "cycle_key"),
    (sql.LifecycleEvent, "cycle_key"),
]


@dataclasses.dataclass(frozen=True)
class Collision:
    kind: str
    key: str
