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

import atr.models.sql as sql
import atr.shared.upload as upload


@pytest.mark.parametrize(
    ("committee_key", "is_podling", "expected"),
    [
        ("example", True, "dev/incubator/example"),
        ("example", False, "dev/example"),
        (None, False, "dev/example-component"),
    ],
)
def test_svn_import_base_path(committee_key: str | None, is_podling: bool, expected: str) -> None:
    committee = sql.Committee(key=committee_key, is_podling=is_podling) if committee_key else None
    project = sql.Project(key="example-component", committee_key=committee_key, committee=committee)

    assert str(upload.svn_import_base_path(project, upload.SvnArea.DEV)) == expected
