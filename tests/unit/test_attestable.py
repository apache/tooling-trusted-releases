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
        "dist/apache-widget-1.0-src.tar.gz": "h1",
        "dist/apache-widget-1.0.zip": "h1",
        "other/apache-widget-1.0.zip": "h1",
        "docs/readme.txt": "h2",
    }
    path_to_size = {
        "dist/apache-widget-1.0-src.tar.gz": 100,
        "dist/apache-widget-1.0.zip": 100,
        "other/apache-widget-1.0.zip": 100,
        "docs/readme.txt": 50,
    }

    data = attestable._generate_files_data(
        path_to_hash=path_to_hash,
        path_to_size=path_to_size,
        revision_number="00002",
        release_policy=None,
        uploader_uid="bob",
        previous=previous,
    )

    assert data.hashes["h1"].basenames == ["apache-widget-1.0-src.tar.gz", "apache-widget-1.0.zip"]
    assert data.hashes["h1"].uploaders == [("alice", "00001"), ("bob", "00002")]
    assert data.hashes["h2"].basenames == ["readme.txt"]
