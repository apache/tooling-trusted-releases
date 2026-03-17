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

import atr.attestable as attestable
import atr.models.attestable as models
import atr.models.sql as sql


def test_can_write_file_state_rows_first_revision():
    assert attestable.can_write_file_state_rows(previous=None, parent_name=None) is True


def test_can_write_file_state_rows_missing_attestable_with_parent():
    assert attestable.can_write_file_state_rows(previous=None, parent_name="proj-1.0 00005") is False


def test_can_write_file_state_rows_v1_previous():
    previous = models.AttestableV1(paths={"a.tar.gz": "h1"})
    assert attestable.can_write_file_state_rows(previous=previous, parent_name="proj-1.0 00002") is False


def test_can_write_file_state_rows_v2_previous():
    previous = models.AttestableV2(
        paths={
            "a.tar.gz": models.PathEntryV2(content_hash="h1", classification="source"),
        }
    )
    assert attestable.can_write_file_state_rows(previous=previous, parent_name="proj-1.0 00002") is True


def test_compute_file_state_rows_changed_classification():
    previous = models.AttestableV2(
        paths={
            "app.tar.gz": models.PathEntryV2(content_hash="hash1", classification="metadata"),
        },
    )
    rows = attestable.compute_file_state_rows(
        "example-0.0.1",
        2,
        {"app.tar.gz": "hash1"},
        {"app.tar.gz": "source"},
        previous,
    )

    assert len(rows) == 1
    assert rows[0].path == "app.tar.gz"
    assert rows[0].present is True
    assert rows[0].content_hash == "hash1"
    assert rows[0].classification == "source"


def test_compute_file_state_rows_changed_hash():
    previous = models.AttestableV2(
        paths={
            "README.md": models.PathEntryV2(content_hash="hash1", classification="metadata"),
        },
    )
    rows = attestable.compute_file_state_rows(
        "example-0.0.1",
        2,
        {"README.md": "hash2"},
        {"README.md": "metadata"},
        previous,
    )

    assert len(rows) == 1
    assert rows[0].path == "README.md"
    assert rows[0].present is True
    assert rows[0].content_hash == "hash2"
    assert rows[0].classification == "metadata"


def test_compute_file_state_rows_deleted_path():
    previous = models.AttestableV2(
        paths={
            "old-file.tar.gz": models.PathEntryV2(content_hash="hash1", classification="source"),
        },
    )
    rows = attestable.compute_file_state_rows("example-0.0.1", 2, {}, {}, previous)

    assert len(rows) == 1
    assert rows[0].path == "old-file.tar.gz"
    assert rows[0].present is False
    assert rows[0].content_hash is None
    assert rows[0].classification is None


def test_compute_file_state_rows_new_path():
    rows = attestable.compute_file_state_rows(
        "example-0.0.1",
        1,
        {"README.md": "hash1"},
        {"README.md": "metadata"},
        None,
    )

    assert len(rows) == 1
    assert rows[0].release_key == "example-0.0.1"
    assert rows[0].path == "README.md"
    assert rows[0].since_revision_seq == 1
    assert rows[0].present is True
    assert rows[0].content_hash == "hash1"
    assert rows[0].classification == "metadata"


def test_compute_file_state_rows_unchanged_path():
    previous = models.AttestableV2(
        paths={
            "README.md": models.PathEntryV2(content_hash="hash1", classification="metadata"),
        },
    )
    rows = attestable.compute_file_state_rows(
        "example-0.0.1",
        2,
        {"README.md": "hash1"},
        {"README.md": "metadata"},
        previous,
    )

    assert len(rows) == 0


def test_compute_file_state_rows_v1_previous():
    previous = models.AttestableV1(paths={"README.md": "hash1"})
    rows = attestable.compute_file_state_rows(
        "example-0.0.1",
        2,
        {"README.md": "hash1"},
        {"README.md": "metadata"},
        previous,
    )

    assert len(rows) == 1
    assert rows[0].present is True
    assert rows[0].content_hash == "hash1"
    assert rows[0].classification == "metadata"


def test_release_file_state_deleted():
    state = sql.ReleaseFileState(
        release_key="example-0.0.1",
        path="removed-file.tar.gz",
        since_revision_seq=3,
        present=False,
        content_hash=None,
        classification=None,
    )

    assert state.release_key == "example-0.0.1"
    assert state.path == "removed-file.tar.gz"
    assert state.since_revision_seq == 3
    assert state.present is False
    assert state.content_hash is None
    assert state.classification is None


def test_release_file_state_present():
    state = sql.ReleaseFileState(
        release_key="example-0.0.1",
        path="apache-example-0.0.1-src.tar.gz",
        since_revision_seq=1,
        present=True,
        content_hash="blake3:7f83b1657ff1fc",
        classification="source",
    )

    assert state.release_key == "example-0.0.1"
    assert state.path == "apache-example-0.0.1-src.tar.gz"
    assert state.since_revision_seq == 1
    assert state.present is True
    assert state.content_hash == "blake3:7f83b1657ff1fc"
    assert state.classification == "source"
