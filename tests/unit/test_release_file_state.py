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


def test_release_file_state_deleted():
    state = sql.ReleaseFileState(
        release_name="example-0.0.1",
        path="removed-file.tar.gz",
        since_revision_seq=3,
        present=False,
        content_hash=None,
        classification=None,
    )

    assert state.release_name == "example-0.0.1"
    assert state.path == "removed-file.tar.gz"
    assert state.since_revision_seq == 3
    assert state.present is False
    assert state.content_hash is None
    assert state.classification is None


def test_release_file_state_present():
    state = sql.ReleaseFileState(
        release_name="example-0.0.1",
        path="apache-example-0.0.1-src.tar.gz",
        since_revision_seq=1,
        present=True,
        content_hash="blake3:7f83b1657ff1fc",
        classification="source",
    )

    assert state.release_name == "example-0.0.1"
    assert state.path == "apache-example-0.0.1-src.tar.gz"
    assert state.since_revision_seq == 1
    assert state.present is True
    assert state.content_hash == "blake3:7f83b1657ff1fc"
    assert state.classification == "source"
