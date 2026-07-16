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
