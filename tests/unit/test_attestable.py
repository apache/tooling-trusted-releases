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
        hashes={"h1": models.HashEntryV2(size=100, uploaders=[("alice", "00001")])},
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
    assert loaded.paths["a.tar.gz"].provenance is None
    assert loaded.paths["a.tar.gz.sha512"].content_hash == "h2"
    assert loaded.paths["a.tar.gz.sha512"].classification == "metadata"


def test_attestable_v2_round_trip_with_provenance():
    provenance = models.ProvenanceV2(
        generator=models.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "fingerprint": "abcdef",
            "initiated_by": "alice",
            "signature_path": "a.tar.gz.asc",
            "source_content_hashes": {"a.tar.gz": "h1"},
            "source_paths": ["a.tar.gz"],
        },
    )
    original = models.AttestableV2(
        paths={
            "a.tar.gz": models.PathEntryV2(content_hash="h1", classification="source"),
            "a.tar.gz.sha512": models.PathEntryV2(
                content_hash="h2",
                classification="metadata",
                provenance=provenance,
            ),
        },
    )

    loaded = models.AttestableV2.model_validate_json(original.model_dump_json())

    assert loaded == original
    entry = loaded.paths["a.tar.gz.sha512"]
    assert entry.provenance is not None
    assert entry.provenance.generator == models.GeneratorV2.SHA512_FROM_SIGNATURE
    assert entry.provenance.metadata["signature_path"] == "a.tar.gz.asc"
    assert loaded.paths["a.tar.gz"].provenance is None


def test_effective_path_provenance_caller_wins():
    caller_entry = models.ProvenanceV2(
        generator=models.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={"initiated_by": "bob", "source_paths": ["a.tar.gz"]},
    )
    inherited = models.ProvenanceV2(
        generator=models.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={"initiated_by": "alice", "source_paths": ["a.tar.gz"]},
    )
    previous = models.AttestableV2(
        paths={
            "a.tar.gz": models.PathEntryV2(content_hash="h1", classification="source"),
            "a.tar.gz.sha512": models.PathEntryV2(content_hash="h2", classification="metadata", provenance=inherited),
        },
    )
    result = attestable.effective_path_provenance(
        {safe.RelPath("a.tar.gz.sha512"): caller_entry},
        {safe.RelPath("a.tar.gz"): "h1", safe.RelPath("a.tar.gz.sha512"): "h2"},
        previous,
    )

    assert result == {"a.tar.gz.sha512": caller_entry}


def test_effective_path_provenance_drops_inheritance_when_hash_changes():
    inherited = models.ProvenanceV2(
        generator=models.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={"initiated_by": "alice", "source_paths": ["a.tar.gz"]},
    )
    previous = models.AttestableV2(
        paths={
            "a.tar.gz.sha512": models.PathEntryV2(content_hash="h2", classification="metadata", provenance=inherited),
        },
    )
    result = attestable.effective_path_provenance(
        None,
        {safe.RelPath("a.tar.gz.sha512"): "h2-changed"},
        previous,
    )

    assert result == {}


def test_effective_path_provenance_v1_previous_returns_empty():
    previous = models.AttestableV1(paths={"a.tar.gz.sha512": "h2"})
    result = attestable.effective_path_provenance(
        None,
        {safe.RelPath("a.tar.gz.sha512"): "h2"},
        previous,
    )

    assert result == {}


async def test_generate_files_data_preserves_inherited_provenance_for_unchanged_paths():
    inherited = models.ProvenanceV2(
        generator=models.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "initiated_by": "alice",
            "source_paths": ["apache-widget-1.0-src.tar.gz"],
        },
    )
    previous = models.AttestableV2(
        paths={
            "apache-widget-1.0-src.tar.gz": models.PathEntryV2(content_hash="h1", classification="source"),
            "apache-widget-1.0-src.tar.gz.sha512": models.PathEntryV2(
                content_hash="h2", classification="metadata", provenance=inherited
            ),
        },
    )
    path_to_hash = {
        safe.RelPath("apache-widget-1.0-src.tar.gz"): "h1",
        safe.RelPath("apache-widget-1.0-src.tar.gz.sha512"): "h2",
        safe.RelPath("README.md"): "h3",
    }
    effective = attestable.effective_path_provenance(None, path_to_hash, previous)

    data = await attestable._generate_files_data(
        path_to_hash=path_to_hash,
        path_to_size={
            safe.RelPath("apache-widget-1.0-src.tar.gz"): 100,
            safe.RelPath("apache-widget-1.0-src.tar.gz.sha512"): 64,
            safe.RelPath("README.md"): 50,
        },
        revision_number=safe.RevisionNumber("00002"),
        release_policy=None,
        uploader_uid="bob",
        previous=previous,
        base_path=safe.StatePath(pathlib.Path("/test")),
        effective_path_provenance=effective,
    )

    assert data.paths["apache-widget-1.0-src.tar.gz.sha512"].provenance == inherited
    assert data.paths["apache-widget-1.0-src.tar.gz"].provenance is None
    assert data.paths["README.md"].provenance is None


async def test_generate_files_data_returns_attestable_v2():
    data = await attestable._generate_files_data(
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


async def test_generate_files_data_writes_provenance_only_for_generated_path():
    provenance = models.ProvenanceV2(
        generator=models.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={
            "initiated_by": "alice",
            "source_paths": ["apache-widget-1.0-src.tar.gz"],
        },
    )
    data = await attestable._generate_files_data(
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
        effective_path_provenance={"apache-widget-1.0-src.tar.gz.sha512": provenance},
    )

    assert data.paths["apache-widget-1.0-src.tar.gz"].provenance is None
    assert data.paths["apache-widget-1.0-src.tar.gz.sha512"].provenance == provenance


def test_hash_entry_v2_basenames_round_trip():
    entry = models.HashEntryV2(
        size=123,
        uploaders=[("alice", "00001")],
        basenames=["apache-widget-1.0-src.tar.gz"],
    )

    loaded = models.HashEntryV2.model_validate_json(entry.model_dump_json())

    assert loaded == entry
    assert loaded.basenames == ["apache-widget-1.0-src.tar.gz"]


async def test_hash_metadata_basenames_are_cumulative_and_unique():
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        hashes={
            "h1": models.HashEntryV1(
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

    data = await attestable._generate_files_data(
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
    assert isinstance(data.hashes["h1"], models.HashEntryV2)
    assert isinstance(data.hashes["h2"], models.HashEntryV2)


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


def test_path_provenance_returns_entry_for_v2():
    provenance = models.ProvenanceV2(
        generator=models.GeneratorV2.SHA512_FROM_SIGNATURE,
        metadata={"initiated_by": "alice", "source_paths": ["a.tar.gz"]},
    )
    v2 = models.AttestableV2(
        paths={
            "a.tar.gz.sha512": models.PathEntryV2(content_hash="h2", classification="metadata", provenance=provenance),
            "a.tar.gz": models.PathEntryV2(content_hash="h1", classification="source"),
        },
    )

    assert attestable.path_provenance(v2, "a.tar.gz.sha512") == provenance
    assert attestable.path_provenance(v2, "a.tar.gz") is None
    assert attestable.path_provenance(v2, "missing") is None


def test_path_provenance_returns_none_for_v1():
    v1 = models.AttestableV1(paths={"a.tar.gz": "h1"})

    assert attestable.path_provenance(v1, "a.tar.gz") is None
