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

import atr.analysis as analysis


def test_artifact_stem() -> None:
    assert analysis.artifact_stem("apache-example-1.0.tar.gz") == "apache-example-1.0"
    assert analysis.artifact_stem("apache-example-1.0.jar") == "apache-example-1.0"
    assert analysis.artifact_stem("apache-example-1.0.pom") == "apache-example-1.0"
    # Nothing without an artifact extension has a stem, which keeps an SBOM from standing as the
    # stem of another SBOM
    assert analysis.artifact_stem("KEYS") is None
    assert analysis.artifact_stem("apache-example-1.0.tar.gz.cdx.json") is None
    assert analysis.artifact_stem("apache-example-1.0-cyclonedx.json") is None


def test_sbom_candidates() -> None:
    # Callers take the first candidate they find, so the whole name comes before the stem, which
    # every format of one artifact shares
    assert analysis.sbom_candidates("apache-example-1.0.jar", analysis.CYCLONEDX_JSON_SUFFIXES) == [
        "apache-example-1.0.jar.cdx.json",
        "apache-example-1.0.jar-cyclonedx.json",
        "apache-example-1.0.cdx.json",
        "apache-example-1.0-cyclonedx.json",
    ]
    # A name with no artifact extension has only itself to offer
    assert analysis.sbom_candidates("KEYS", analysis.CYCLONEDX_JSON_SUFFIXES) == [
        "KEYS.cdx.json",
        "KEYS-cyclonedx.json",
    ]
    # The XML suffixes reach the stem too, which is what the cataloguer and announce pair on
    assert "apache-example-1.0-cyclonedx.xml" in analysis.sbom_candidates(
        "apache-example-1.0.jar", analysis.SBOM_SUFFIXES
    )


def test_sbom_candidates_maven_classifier() -> None:
    # The CycloneDX Maven plugin names its output off the version alone, dropping the artifact's
    # own classifier, so a classified artifact still has to reach the declassified SBOM name
    candidates = analysis.sbom_candidates(
        "atr-maven-plugin-1.0.0-beta-2-SNAPSHOT-source-release.zip", analysis.CYCLONEDX_JSON_SUFFIXES
    )
    assert "atr-maven-plugin-1.0.0-beta-2-SNAPSHOT-cyclonedx.json" in candidates


def test_sbom_candidates_module_sbom_at_release_root() -> None:
    # A module SBOM names itself off the version and may sit at the release root while the artifacts
    # sit in subdirectories, so a subdirectory artifact must reach the root-level SBOM
    candidates = analysis.sbom_candidates(
        "binaries/apache-maven-4.1.0-SNAPSHOT-bin.tar.gz", analysis.CYCLONEDX_JSON_SUFFIXES
    )
    assert "binaries/apache-maven-4.1.0-SNAPSHOT-cyclonedx.json" in candidates
    assert "apache-maven-4.1.0-SNAPSHOT-cyclonedx.json" in candidates
    # The artifact's own directory is searched before the root, so a co-located SBOM wins
    own_dir = candidates.index("binaries/apache-maven-4.1.0-SNAPSHOT-cyclonedx.json")
    root = candidates.index("apache-maven-4.1.0-SNAPSHOT-cyclonedx.json")
    assert own_dir < root


def test_sbom_pairs_artifact() -> None:
    # The inverse of sbom_candidates: an SBOM pairs with an artifact when it is one of the names that
    # artifact would generate, so the whole-name, stem, and dropped-classifier styles all pair
    assert analysis.sbom_pairs_artifact(
        "apache-example-1.0.tar.gz.cdx.json", "apache-example-1.0.tar.gz", analysis.SBOM_SUFFIXES
    )
    assert analysis.sbom_pairs_artifact(
        "apache-example-1.0-cyclonedx.json", "apache-example-1.0.jar", analysis.SBOM_SUFFIXES
    )
    assert analysis.sbom_pairs_artifact(
        "atr-maven-plugin-1.0.0-beta-2-SNAPSHOT-cyclonedx.json",
        "atr-maven-plugin-1.0.0-beta-2-SNAPSHOT-source-release.zip",
        analysis.SBOM_SUFFIXES,
    )
    # An SBOM does not pair with an unrelated artifact
    assert not analysis.sbom_pairs_artifact(
        "apache-example-1.0-cyclonedx.json", "apache-other-2.0.jar", analysis.SBOM_SUFFIXES
    )
    # A module SBOM at the release root pairs with an artifact in a subdirectory
    assert analysis.sbom_pairs_artifact(
        "apache-maven-4.1.0-SNAPSHOT-cyclonedx.json",
        "source/apache-maven-4.1.0-SNAPSHOT-src.tar.gz",
        analysis.SBOM_SUFFIXES,
    )


def test_classifier_removed() -> None:
    # A classifier abutting the extension comes off, along with its leading separator
    assert analysis._classifier_removed("apache-example-1.0-source-release.zip") == "apache-example-1.0.zip"
    assert analysis._classifier_removed("apache-example-1.0-bin.tar.gz") == "apache-example-1.0.tar.gz"
    # A name with no classifier is returned untouched, so callers can tell nothing was stripped
    assert analysis._classifier_removed("apache-example-1.0.jar") == "apache-example-1.0.jar"
    # A variant word elsewhere in the name is not a classifier, so it stays put
    assert analysis._classifier_removed("apache-source-tool-1.0.tar.gz") == "apache-source-tool-1.0.tar.gz"
