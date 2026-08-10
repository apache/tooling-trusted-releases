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

import atr.constants as constants
import atr.models.attestable as attestable_models
import atr.models.sql as sql
import atr.shared.published as published


def test_published_files_from_artifact_rows_without_dist_dir() -> None:
    artifacts = [
        sql.Artifact(
            project_key="foo",
            version="1.0",
            artifact_path="apache-foo-1.0.tar.gz",
            signature_path="apache-foo-1.0.tar.gz.asc",
            checksum_path="apache-foo-1.0.tar.gz.sha512",
        )
    ]

    files = published.files(artifacts, None, False)

    assert [f.path for f in files] == [
        "apache-foo-1.0.tar.gz",
        "apache-foo-1.0.tar.gz.asc",
        "apache-foo-1.0.tar.gz.sha512",
    ]
    assert all(f.size is None for f in files)
    assert all(f.url is None for f in files)


def test_published_files_use_archive_host_when_archived() -> None:
    artifacts = [
        sql.Artifact(
            project_key="foo",
            version="1.0",
            artifact_path="apache-foo-1.0.tar.gz",
            signature_path="apache-foo-1.0.tar.gz.asc",
            download_path_suffix="tooling/foo",
        )
    ]

    files = published.files(artifacts, None, True)

    assert [f.url for f in files] == [
        f"{constants.ARCHIVE_APACHE_URL}/tooling/foo/apache-foo-1.0.tar.gz",
        f"{constants.ARCHIVE_APACHE_URL}/tooling/foo/apache-foo-1.0.tar.gz.asc",
    ]


def test_published_files_use_attestable_paths_and_sizes() -> None:
    artifacts = [
        sql.Artifact(
            project_key="foo",
            version="1.0",
            artifact_path="apache-foo-1.0.tar.gz",
            signature_path="apache-foo-1.0.tar.gz.asc",
            download_path_suffix="tooling/foo",
        )
    ]
    attested = attestable_models.AttestableV2(
        hashes={
            "blake3:aa": attestable_models.HashEntryV2(size=1024, uploaders=[("sbp", "00001")]),
            "blake3:bb": attestable_models.HashEntryV2(size=833, uploaders=[("sbp", "00001")]),
        },
        paths={
            "apache-foo-1.0.tar.gz": attestable_models.PathEntryV2(content_hash="blake3:aa", classification="source"),
            "apache-foo-1.0.tar.gz.asc": attestable_models.PathEntryV2(
                content_hash="blake3:bb", classification="metadata"
            ),
            "README.md": attestable_models.PathEntryV2(content_hash="blake3:bb", classification="docs"),
        },
    )

    files = published.files(artifacts, attested, False)

    assert [f.path for f in files] == ["README.md", "apache-foo-1.0.tar.gz", "apache-foo-1.0.tar.gz.asc"]
    by_path = {f.path: f for f in files}
    assert by_path["apache-foo-1.0.tar.gz"].size == 1024
    assert by_path["README.md"].size == 833
    closer = f"{constants.CLOSER_LUA_URL}/tooling/foo/apache-foo-1.0.tar.gz?action=download"
    assert by_path["apache-foo-1.0.tar.gz"].url == closer
    assert (
        by_path["apache-foo-1.0.tar.gz.asc"].url
        == f"{constants.DOWNLOADS_APACHE_URL}/tooling/foo/apache-foo-1.0.tar.gz.asc"
    )
    assert by_path["README.md"].url == f"{constants.DOWNLOADS_APACHE_URL}/tooling/foo/README.md"


def test_published_files_use_each_artifact_row_directory() -> None:
    artifacts = [
        sql.Artifact(
            project_key="foo",
            version="1.0",
            artifact_path="apache-foo-1.0-bin.tar.gz",
            download_path_suffix="foo/binaries",
        ),
        sql.Artifact(
            project_key="foo",
            version="1.0",
            artifact_path="apache-foo-1.0-src.tar.gz",
            download_path_suffix="foo/source",
        ),
        sql.Artifact(project_key="foo", version="1.0", artifact_path="apache-foo-1.0.pom"),
    ]

    files = published.files(artifacts, None, False)

    by_path = {f.path: f for f in files}
    binaries = f"{constants.CLOSER_LUA_URL}/foo/binaries/apache-foo-1.0-bin.tar.gz?action=download"
    source = f"{constants.CLOSER_LUA_URL}/foo/source/apache-foo-1.0-src.tar.gz?action=download"
    assert by_path["apache-foo-1.0-bin.tar.gz"].url == binaries
    assert by_path["apache-foo-1.0-src.tar.gz"].url == source
    assert by_path["apache-foo-1.0.pom"].url is None
