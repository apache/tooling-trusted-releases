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

import pathlib

import atr.attestable as attestable
import atr.models.attestable as models
import atr.models.safe as safe


def test_attestable_v2_round_trip():
    original = models.AttestableV2(
        hashes={"h1": models.HashEntry(size=100, uploaders=[("alice", "00001")])},
        paths={
            "a.tar.gz": models.PathEntryV2(content_hash="h1", classification="source"),
            "a.tar.gz.sha512": models.PathEntryV2(content_hash="h2", classification="metadata"),
        },
        policy={"min_hours": 72},
    )

    loaded = models.AttestableV2.model_validate_json(original.model_dump_json())

    assert loaded == original
    assert loaded.version == 2
    assert loaded.paths["a.tar.gz"].content_hash == "h1"
    assert loaded.paths["a.tar.gz"].classification == "source"
    assert loaded.paths["a.tar.gz.sha512"].content_hash == "h2"
    assert loaded.paths["a.tar.gz.sha512"].classification == "metadata"


def test_generate_files_data_returns_attestable_v2():
    data = attestable._generate_files_data(
        path_to_hash={
            safe.RelPath("apache-widget-1.0-src.tar.gz"): "h1",
            safe.RelPath("apache-widget-1.0-src.tar.gz.sha512"): "h2",
        },
        path_to_size={
            safe.RelPath("apache-widget-1.0-src.tar.gz"): 100,
            safe.RelPath("apache-widget-1.0-src.tar.gz.sha512"): 64,
        },
        revision_number=safe.RevisionNumber("00001"),
        release_policy=None,
        uploader_uid="alice",
        previous=None,
        base_path=safe.StatePath(pathlib.Path("/test")),
    )

    assert isinstance(data, models.AttestableV2)
    assert data.version == 2
    assert data.paths["apache-widget-1.0-src.tar.gz"].content_hash == "h1"
    assert data.paths["apache-widget-1.0-src.tar.gz"].classification == "source"
    assert data.paths["apache-widget-1.0-src.tar.gz.sha512"].content_hash == "h2"
    assert data.paths["apache-widget-1.0-src.tar.gz.sha512"].classification == "metadata"


def test_hash_entry_basenames_round_trip():
    entry = models.HashEntry(
        size=123,
        uploaders=[("alice", "00001")],
        basenames=["apache-widget-1.0-src.tar.gz"],
    )

    loaded = models.HashEntry.model_validate_json(entry.model_dump_json())

    assert loaded == entry
    assert loaded.basenames == ["apache-widget-1.0-src.tar.gz"]


def test_hash_metadata_basenames_are_cumulative_and_unique():
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        hashes={
            "h1": models.HashEntry(
                size=100,
                uploaders=[("alice", "00001")],
                basenames=["apache-widget-1.0-src.tar.gz"],
            )
        },
        policy={},
    )
    path_to_hash = {
        safe.RelPath("dist/apache-widget-1.0-src.tar.gz"): "h1",
        safe.RelPath("dist/apache-widget-1.0.zip"): "h1",
        safe.RelPath("other/apache-widget-1.0.zip"): "h1",
        safe.RelPath("docs/readme.txt"): "h2",
    }
    path_to_size = {
        safe.RelPath("dist/apache-widget-1.0-src.tar.gz"): 100,
        safe.RelPath("dist/apache-widget-1.0.zip"): 100,
        safe.RelPath("other/apache-widget-1.0.zip"): 100,
        safe.RelPath("docs/readme.txt"): 50,
    }

    data = attestable._generate_files_data(
        path_to_hash=path_to_hash,
        path_to_size=path_to_size,
        revision_number=safe.RevisionNumber("00002"),
        release_policy=None,
        uploader_uid="bob",
        previous=previous,
        base_path=safe.StatePath(pathlib.Path("/test")),
    )

    assert data.hashes["h1"].basenames == ["apache-widget-1.0-src.tar.gz", "apache-widget-1.0.zip"]
    assert data.hashes["h1"].uploaders == [("alice", "00001"), ("bob", "00002")]
    assert data.hashes["h2"].basenames == ["readme.txt"]


def test_parse_attestable_v1():
    data = {"version": 1, "paths": {"a.tar.gz": "h1"}, "hashes": {}, "policy": {}}

    result = attestable._parse_attestable(data)

    assert isinstance(result, models.AttestableV1)
    assert result.version == 1
    assert result.paths == {"a.tar.gz": "h1"}


def test_parse_attestable_v2():
    data = {
        "version": 2,
        "paths": {
            "a.tar.gz": {"content_hash": "h1", "classification": "source"},
        },
        "hashes": {},
        "policy": {},
    }

    result = attestable._parse_attestable(data)

    assert isinstance(result, models.AttestableV2)
    assert result.version == 2
    assert result.paths["a.tar.gz"].content_hash == "h1"
    assert result.paths["a.tar.gz"].classification == "source"


def test_path_hashes_support_v1_and_v2():
    v1 = models.AttestableV1(paths={"a.tar.gz": "h1"}, hashes={}, policy={})
    v2 = models.AttestableV2(
        paths={"a.tar.gz": models.PathEntryV2(content_hash="h1", classification="source")},
        hashes={},
        policy={},
    )

    assert attestable.path_hashes(v1) == {"a.tar.gz": "h1"}
    assert attestable.path_hashes(v2) == {"a.tar.gz": "h1"}
    assert attestable.path_hash(v2, "a.tar.gz") == "h1"
    assert attestable.path_classification(v2, "a.tar.gz") == "source"
