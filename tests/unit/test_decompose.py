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

import pytest

import atr.classify as classify
import atr.svn.dist as dist

_SOURCE = classify.FileType.SOURCE
_BINARY = classify.FileType.BINARY

# Each case is a real dist layout we've had to handle, as (committee, dirs-below-committee,
# filename, expected subproject, expected version, expected classification).
_CASES: list[tuple[str, tuple[str, ...], str | None, str | None, str | None, classify.FileType | None]] = [
    # Airflow providers: providers/ is a distribution bucket, so each provider's
    # identity and version come from the filename, not the dir
    (
        "airflow",
        ("providers",),
        "apache_airflow_providers_alibaba-3.3.2.tar.gz",
        "apache_airflow_providers_alibaba",
        "3.3.2",
        _SOURCE,
    ),
    # The same provider under an airflow-version series dir still keys on the filename
    (
        "airflow",
        ("providers", "2.11"),
        "apache_airflow_providers_fab-1.5.4.tar.gz",
        "apache_airflow_providers_fab",
        "1.5.4",
        _SOURCE,
    ),
    # A wheel rides beside the source for the same provider and is a binary build
    (
        "airflow",
        ("providers",),
        "apache_airflow_providers_alibaba-3.3.2-py3-none-any.whl",
        "apache_airflow_providers_alibaba",
        "3.3.2",
        _BINARY,
    ),
    # The providers bundle is calver and has no per-provider suffix
    (
        "airflow",
        ("providers",),
        "apache_airflow_providers-2025-11-03-source.tar.gz",
        "apache_airflow_providers",
        "2025-11-03",
        _SOURCE,
    ),
    # The older flat layout (file directly under the committee) already worked
    (
        "airflow",
        (),
        "apache_airflow_providers_airbyte-5.0.2.tar.gz",
        "apache_airflow_providers_airbyte",
        "5.0.2",
        _SOURCE,
    ),
    (
        "airflow",
        (),
        "apache_airflow_providers_airbyte-5.0.2-py3-none-any.whl",
        "apache_airflow_providers_airbyte",
        "5.0.2",
        _BINARY,
    ),
    # The airflow core itself is a TLP release: the name is just the committee
    ("airflow", (), "apache-airflow-2.5.0.tar.gz", None, "2.5.0", _SOURCE),
    # AGE keeps its version in the dir and the filename name is the committee, so it
    # stays a TLP release - the dir is not echoed in the filename, so not a bucket
    ("age", ("1.0.0",), "apache-age-1.0.0-incubating-src.tar.gz", None, "1.0.0-incubating", _SOURCE),
    # Sling encodes the subproject in a flat filename under the committee
    ("sling", (), "adapter-annotations-1.0.0-source-release.zip", "adapter-annotations", "1.0.0", _SOURCE),
    # Commons names the subproject in the dir; source/ and binaries/ only split the build,
    # and the subproject resolves to commons-attributes later via missing_project_key
    ("commons", ("attributes", "source"), "commons-attributes-2.2-src.tar.gz", "attributes", "2.2", _SOURCE),
    ("commons", ("attributes", "binaries"), "commons-attributes-2.2.tar.gz", "attributes", "2.2", _BINARY),
    # A self-prefixed area (jackrabbit/jackrabbit) is the TLP's own, not a subproject
    ("jackrabbit", ("jackrabbit", "2.20.1"), "jackrabbit-2.20.1.tar.gz", None, "2.20.1", _SOURCE),
    # A combined name-version dir splits into subproject and version
    ("spark", ("spark-kubernetes-operator-0.8.0",), None, "spark-kubernetes-operator", "0.8.0", None),
    # A numeric build tail stays part of the version (javaee-api 8.0 build 6)
    ("tomee", ("javaee-api-8.0-6",), "javaee-api-8.0-6-source-release.zip", "javaee-api", "8.0-6", _SOURCE),
    # A flat file with a digit mid-name (sharepoint-2007-plugin) keeps it in the subproject and
    # comes apart at the trailing version, since the filename names the version directly
    (
        "manifoldcf",
        (),
        "apache-manifoldcf-sharepoint-2007-plugin-0.5-src.tar.gz",
        "apache-manifoldcf-sharepoint-2007-plugin",
        "0.5",
        _SOURCE,
    ),
    # A qualifier tail stays part of the version
    ("spark", (), "spark-4.1.0-preview1.tar.gz", None, "4.1.0-preview1", _SOURCE),
    # Kafka's binary carries the Scala version as a distractor; the dir pins the real one,
    # so the binary and the source below land on the same release. The plain .tgz binary
    # has no marker, so it falls to source, but both still attach to kafka 4.3.1
    ("kafka", ("4.3.1",), "kafka_2.13-4.3.1.tgz", None, "4.3.1", _SOURCE),
    ("kafka", ("4.3.1",), "kafka-4.3.1-src.tgz", None, "4.3.1", _SOURCE),
    # A versioned sub-component inside a release's version dir is an artifact of that release, so
    # the dir version holds and the component's own version is ignored - otherwise each binding
    # forks a spurious release (opendal ships a dozen language bindings at independent versions)
    ("opendal", ("0.45.1",), "apache-opendal-bindings-go-0.0.0-src.tar.gz", None, "0.45.1", _SOURCE),
    # A component that happens to share the release version still lands on it
    ("opendal", ("0.45.1",), "apache-opendal-core-0.45.1-src.tar.gz", None, "0.45.1", _SOURCE),
    # A sub-component versioned higher than its release (an integration at 1.0.2 under 0.51.0) still
    # collapses onto the release, so it can't mint a spurious higher release
    ("opendal", ("0.51.0",), "apache-opendal-integrations-compat-1.0.2-src.tar.gz", None, "0.51.0", _SOURCE),
    # A build variant whose version refines the dir (a hadoop1 build of the 0.98.13 release) keeps
    # the filename version, since it's the same release built differently
    ("hbase", ("0.98.13",), "hbase-0.98.13-hadoop1-bin.tar.gz", None, "0.98.13", _BINARY),
    # A sub-component published in its own name-version dir, not inside a release dir, stays a
    # distinct release
    ("hbase", ("hbase-connectors-1.0.0",), "hbase-connectors-1.0.0-src.tar.gz", "hbase-connectors", "1.0.0", _SOURCE),
    # A series dir is still less specific than the filename, which pins the point release
    ("example", ("4.5",), "example-4.5.13.tar.gz", None, "4.5.13", _SOURCE),
    # A leading source/ dir is a build bucket, so a flat TLP release reads from the filename
    ("myfaces", ("source",), "myfaces-1.0.9-src.tgz", None, "1.0.9", _SOURCE),
    ("ant", ("source",), "apache-ant-1.10.0-src.tar.bz2", None, "1.10.0", _SOURCE),
    # A named subproject still comes from the filename under the bucket (old jakarta naming)
    ("struts", ("source",), "jakarta-struts-1.2.2-src.tar.gz", "jakarta-struts", "1.2.2", _SOURCE),
    # A version dir inside source/ is ignored because the filename pins the version
    ("couchdb", ("source", "1.0.3"), "apache-couchdb-1.0.3.tar.gz", None, "1.0.3", _SOURCE),
    # A releases/ container is a bucket too
    ("cloudstack", ("releases", "4.0.2"), "apache-cloudstack-4.0.2-src.tar.bz2", None, "4.0.2", _SOURCE),
    # plugins/ is a bucket only under maven, where each plugin is its own release
    ("maven", ("plugins",), "maven-acr-plugin-1.0-source-release.zip", "maven-acr-plugin", "1.0", _SOURCE),
    # An underscore-separated version dir normalises to dots for the release key (the real
    # path keeps its underscores, so downloads still resolve)
    ("santuario", ("java-library", "1_4_7"), "xml-security-src-1_4_7.zip", "java-library", "1.4.7", _SOURCE),
    ("ws", ("axis2", "0_92"), "Axis2Ideaplugin.jar", "axis2", "0.92", _BINARY),
    # A build/status suffix that leaked into the filename name is stripped from the subproject
    ("chukwa", (), "chukwa-src-0.6.0.tar.gz", None, "0.6.0", _SOURCE),
    ("tapestry", (), "tapestry-src-5.0.10.tar.bz2", None, "5.0.10", _SOURCE),
    ("chukwa", (), "chukwa-incubating-src-0.5.0.tar.gz", None, "0.5.0", _SOURCE),
    # A v-prefixed version leaves a stray v on the name; strip it so the release merges with
    # the non-v releases (apache- prefix is stripped later, at resolution)
    (
        "cloudstack",
        ("releases", "kubernetes-provider-v1.0.0"),
        "apache-cloudstack-kubernetes-provider-v1.0.0-src.tar.bz2",
        "apache-cloudstack-kubernetes-provider",
        "1.0.0",
        _SOURCE,
    ),
    # A qualifier build number after a separator is kept, so alpha-1 and alpha-2 stay distinct
    ("accumulo", (), "accumulo-2.0.0-alpha-1.tar.gz", None, "2.0.0-alpha-1", _SOURCE),
    ("accumulo", (), "accumulo-2.0.0-alpha-2.tar.gz", None, "2.0.0-alpha-2", _SOURCE),
    ("continuum", (), "apache-continuum-1.1-beta-4.tar.gz", None, "1.1-beta-4", _SOURCE),
    # The version qualifier is lowercased so a RC1/rc1 case split merges to one release
    ("turbine", ("2.2-RC1",), None, None, "2.2-rc1", None),
    ("turbine", (), "turbine-2.2-RC2.tar.gz", None, "2.2-rc2", _SOURCE),
]


@pytest.mark.parametrize(("committee", "parts", "filename", "subproject", "version", "classification"), _CASES)
def test_decompose_known_dist_layouts(
    committee: str,
    parts: tuple[str, ...],
    filename: str | None,
    subproject: str | None,
    version: str | None,
    classification: classify.FileType | None,
) -> None:
    if (filename is not None) and (classification is not None):
        full_path = pathlib.PurePosixPath(committee, *parts, filename)
        assert classify.classify_path(full_path) == classification
    result = dist.decompose(committee, parts, filename)
    assert result is not None
    assert result.subproject == subproject
    assert result.version == version


def test_repos_subtree_is_not_a_release() -> None:
    # A package repository under a release dir holds bundled components, not releases
    result = dist.decompose(
        "incubator",
        ("bigtop", "bigtop-0.2.0-incubating", "repos", "centos5", "hadoop"),
        "hadoop-0.20.205.0.2-1.x86_64.rpm",
    )
    assert result is None


def test_candidate_keys_bridge_underscore_package_names() -> None:
    # An underscored dist package name bridges to the registry's hyphenated key, so candidate_keys
    # offers the hyphenated form rather than only the underscore duplicate
    assert "airflow-providers-alibaba" in dist.candidate_keys("airflow", "apache_airflow_providers_alibaba")
    assert "airflow-providers" in dist.candidate_keys("airflow", "apache_airflow_providers")
    # An apache- prefixed name still strips to the bare key, as before
    assert dist.candidate_keys("cassandra", "apache-cassandra")[0] == "cassandra"
