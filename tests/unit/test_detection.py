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

import atr.detection as detection
import atr.models.attestable as models


def test_deduplicate_quarantine_archives_different_extensions_kept_separately():
    paths_list = ["a/src.tar.gz", "a/src.zip"]
    path_to_hash = {"a/src.tar.gz": "h1", "a/src.zip": "h1"}

    result = detection.deduplicate_quarantine_archives(paths_list, path_to_hash)

    assert len(result) == 2


def test_deduplicate_quarantine_archives_empty_input():
    result = detection.deduplicate_quarantine_archives([], {})

    assert result == []


def test_deduplicate_quarantine_archives_keeps_smallest_rel_path_per_hash_suffix():
    paths_list = ["b/archive.tar.gz", "a/archive.tar.gz", "c/other.zip"]
    path_to_hash = {
        "a/archive.tar.gz": "h1",
        "b/archive.tar.gz": "h1",
        "c/other.zip": "h2",
    }

    result = detection.deduplicate_quarantine_archives(paths_list, path_to_hash)

    assert result == [("a/archive.tar.gz", "h1"), ("c/other.zip", "h2")]


def test_deduplicate_quarantine_archives_tgz_normalises_to_tar_gz():
    paths_list = ["a/src.tgz", "b/src.tar.gz"]
    path_to_hash = {"a/src.tgz": "h1", "b/src.tar.gz": "h1"}

    result = detection.deduplicate_quarantine_archives(paths_list, path_to_hash)

    assert len(result) == 1
    assert result[0][1] == "h1"


def test_detect_archives_requiring_quarantine_known_hash_and_different_extension():
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tgz": "h1"},
        hashes={"h1": models.HashEntry(size=100, uploaders=[("alice", "00001")], basenames=["old-src.tgz"])},
        policy={},
    )

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.0.zip": "h1"},
        previous_attestable=previous,
    )

    assert result == ["dist/apache-widget-1.0.zip"]


def test_detect_archives_requiring_quarantine_known_hash_and_same_extension():
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        hashes={
            "h1": models.HashEntry(
                size=100,
                uploaders=[("alice", "00001")],
                basenames=["apache-widget-0.9-src.tar.gz"],
            )
        },
        policy={},
    )

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        previous_attestable=previous,
    )

    assert result == []


def test_detect_archives_requiring_quarantine_missing_historical_basenames():
    hash_entry = models.HashEntry(size=100, uploaders=[("alice", "00001")])
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        hashes={"h1": hash_entry},
        policy={},
    )

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.1-src.tar.gz": "h1"},
        previous_attestable=previous,
    )

    assert "basenames" not in hash_entry.model_fields_set
    assert result == ["dist/apache-widget-1.1-src.tar.gz"]


def test_detect_archives_requiring_quarantine_new_hash_new_extension():
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tar.gz": "h_old"},
        hashes={"h_old": models.HashEntry(size=100, uploaders=[("alice", "00001")], basenames=["old-src.tar.gz"])},
        policy={},
    )

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.1.zip": "h_new"},
        previous_attestable=previous,
    )

    assert result == ["dist/apache-widget-1.1.zip"]


def test_detect_archives_requiring_quarantine_no_previous_attestable():
    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        previous_attestable=None,
    )

    assert result == ["dist/apache-widget-1.0-src.tar.gz"]


def test_detect_archives_requiring_quarantine_non_archive_files_are_ignored():
    previous = models.AttestableV1(paths={}, hashes={}, policy={})

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/README.md": "h1", "dist/KEYS": "h2"},
        previous_attestable=previous,
    )

    assert result == []


def test_detect_archives_requiring_quarantine_tgz_and_tar_gz_are_equivalent():
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

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.0-src.tgz": "h1"},
        previous_attestable=previous,
    )

    assert result == []


@pytest.mark.parametrize("alias", [".jar", ".war", ".whl", ".nar", ".nbm", ".vsix", ".apk"])
def test_deduplicate_quarantine_archives_zip_family_aliases_collapse_with_zip(alias):
    paths_list = [f"a/artifact{alias}", "b/artifact.zip"]
    path_to_hash = {f"a/artifact{alias}": "h1", "b/artifact.zip": "h1"}

    result = detection.deduplicate_quarantine_archives(paths_list, path_to_hash)

    assert len(result) == 1


@pytest.mark.parametrize("suffix", [".jar", ".war", ".whl", ".nar", ".nbm", ".vsix", ".apk"])
def test_detect_archives_requiring_quarantine_zip_family_new_upload(suffix):
    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={f"dist/widget{suffix}": "h1"},
        previous_attestable=None,
    )

    assert result == [f"dist/widget{suffix}"]


@pytest.mark.parametrize(
    ("prev_suffix", "new_suffix"),
    [
        (".jar", ".zip"),
        (".war", ".zip"),
        (".whl", ".zip"),
        (".nar", ".zip"),
        (".nbm", ".zip"),
        (".vsix", ".zip"),
        (".apk", ".zip"),
        (".jar", ".war"),
        (".war", ".whl"),
        (".nar", ".nbm"),
        (".vsix", ".apk"),
    ],
)
def test_detect_archives_requiring_quarantine_zip_family_aliases_are_equivalent(prev_suffix, new_suffix):
    previous = models.AttestableV1(
        paths={f"dist/widget{prev_suffix}": "h1"},
        hashes={
            "h1": models.HashEntry(
                size=100,
                uploaders=[("alice", "00001")],
                basenames=[f"widget{prev_suffix}"],
            )
        },
        policy={},
    )

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={f"dist/widget{new_suffix}": "h1"},
        previous_attestable=previous,
    )

    assert result == []


@pytest.mark.parametrize(
    "filename",
    [
        "a.tar.gz",
        "a.tgz",
        "a.zip",
        "a.jar",
        "a.war",
        "a.whl",
        "a.nar",
        "a.nbm",
        "a.vsix",
        "a.apk",
        "FOO.JAR",
        "Widget-1.0.War",
        "package-1.0.WHL",
        "processor-1.0.NAR",
        "module-1.0.NBM",
        "extension-1.0.VSIX",
        "app-1.0.APK",
    ],
)
def test_is_quarantine_archive_accepts_zip_family_and_tar_family(filename):
    assert detection.is_quarantine_archive(filename) is True


@pytest.mark.parametrize(
    "filename",
    ["README.md", "KEYS", "widget.txt", "widget.tar.gz.asc", "widget.tar", "widget.7z"],
)
def test_is_quarantine_archive_rejects_non_quarantine_files(filename):
    assert detection.is_quarantine_archive(filename) is False
