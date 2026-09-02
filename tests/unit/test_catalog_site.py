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

import atr.catalog_site as catalog_site
import atr.models.api as api
import atr.models.safe as safe


def _artifact(
    path: str,
    url: str | None,
    *,
    classification: str | None = None,
    signature_path: str | None = None,
    signature_url: str | None = None,
    checksum_path: str | None = None,
    checksum_url: str | None = None,
    sbom_path: str | None = None,
    sbom_url: str | None = None,
) -> api.CatalogArtifact:
    return api.CatalogArtifact(
        artifact_path=path,
        classification=classification,
        signature_path=signature_path,
        checksum_path=checksum_path,
        sbom_path=sbom_path,
        key_fingerprint=None,
        svn_revision=None,
        managed=False,
        dated=None,
        downloadable=url is not None,
        artifact_url=url,
        signature_url=signature_url,
        checksum_url=checksum_url,
        sbom_url=sbom_url,
    )


def _version(artifacts: list[api.CatalogArtifact], version: str = "1.0.0") -> api.CatalogVersion:
    return api.CatalogVersion(
        version=safe.VersionKey(version),
        status="released",
        released=None,
        svn_revision=None,
        managed=False,
        cycle=None,
        artifacts=artifacts,
    )


def test_htaccess_maps_each_downloadable_artifact_by_its_file_name_qualifier() -> None:
    version = _version(
        [
            _artifact("apache-x-1.0.0-src.tgz", "https://mirror.example/x/apache-x-1.0.0-src.tgz?action=download"),
            _artifact("apache-x-1.0.0-bin.zip", "https://mirror.example/x/apache-x-1.0.0-bin.zip?action=download"),
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    lines = htaccess.splitlines()
    assert lines[0] == "RewriteEngine On"
    assert 'RewriteCond %{QUERY_STRING} "(^|&)file_name=apache\\-x\\-1\\.0\\.0\\-src\\.tgz(&|$)"' in lines
    assert 'RewriteRule "^$" "https://mirror.example/x/apache-x-1.0.0-src.tgz?action=download" [R=302,NE,L]' in lines
    # RewriteEngine, then per artifact a Cond/Rule pair by file_name, by subpath, and by its
    # unique ext (src.tgz vs bin.zip differ by extension)
    assert len(lines) == 13


def test_htaccess_addresses_the_artifact_by_the_tail_after_the_version() -> None:
    # When the file name carries the release version, subpath is the tail after it - so
    # apache-activemq-5.19.10-bin.tar.gz at 5.19.10 becomes bin.tar.gz.
    version = _version(
        [
            _artifact(
                "apache-activemq-5.19.10-bin.tar.gz",
                "https://mirror.example/activemq/apache-activemq-5.19.10-bin.tar.gz?action=download",
            )
        ],
        version="5.19.10",
    )

    htaccess = catalog_site._release_htaccess(version, "activemq", "activemq")

    assert htaccess is not None
    assert 'RewriteCond %{QUERY_STRING} "(^|&)subpath=bin\\.tar\\.gz(&|$)"' in htaccess


def test_htaccess_subpath_strips_name_tokens_when_the_version_is_absent() -> None:
    # A component bundle names files at the component's own version, so the release version
    # (2020-12) isn't in the file name. subpath then strips apache and the PMC/project names,
    # keeping the component name and its own version.
    version = _version(
        [
            _artifact(
                "apache-airflow-providers-amazon-1.0.0-bin.tar.gz",
                "https://mirror.example/airflow/apache-airflow-providers-amazon-1.0.0-bin.tar.gz?action=download",
            )
        ],
        version="2020-12",
    )

    htaccess = catalog_site._release_htaccess(version, "airflow", "airflow-providers")

    assert htaccess is not None
    assert 'RewriteCond %{QUERY_STRING} "(^|&)subpath=amazon\\-1\\.0\\.0\\-bin\\.tar\\.gz(&|$)"' in htaccess


def test_htaccess_omits_subpath_when_two_artifacts_share_the_tail() -> None:
    # Both files strip to the same tail (bin.tar.gz), so subpath can't name one - it's dropped
    # and file_name still addresses each.
    version = _version(
        [
            _artifact(
                "apache-x-1.0.0-bin.tar.gz", "https://mirror.example/x/apache-x-1.0.0-bin.tar.gz?action=download"
            ),
            _artifact("x-1.0.0-bin.tar.gz", "https://mirror.example/x/x-1.0.0-bin.tar.gz?action=download"),
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    assert "subpath=" not in htaccess
    assert htaccess.count("file_name=") == 2


def test_htaccess_addresses_artifacts_by_their_class() -> None:
    # The stored classification maps to a short class token: source -> src, binary -> bin.
    version = _version(
        [
            _artifact(
                "apache-x-1.0.0-src.tgz",
                "https://mirror.example/x/apache-x-1.0.0-src.tgz?action=download",
                classification="source",
            ),
            _artifact(
                "apache-x-1.0.0-bin.zip",
                "https://mirror.example/x/apache-x-1.0.0-bin.zip?action=download",
                classification="binary",
            ),
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    assert 'RewriteCond %{QUERY_STRING} "(^|&)class=src(&|$)"' in htaccess
    assert 'RewriteCond %{QUERY_STRING} "(^|&)class=bin(&|$)"' in htaccess


def test_htaccess_omits_classifiers_when_two_artifacts_share_all_of_them() -> None:
    # Two source tarballs identical in every classifier (class and ext both match, no os/arch):
    # no classifier combo can name one, so none is emitted - subpath and file_name still do.
    version = _version(
        [
            _artifact(
                "apache-x-1.0.0-src.tar.gz",
                "https://mirror.example/x/apache-x-1.0.0-src.tar.gz?action=download",
                classification="source",
            ),
            _artifact(
                "apache-x-1.0.0-source.tar.gz",
                "https://mirror.example/x/apache-x-1.0.0-source.tar.gz?action=download",
                classification="source",
            ),
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    assert "class=" not in htaccess
    assert "ext=" not in htaccess
    assert "subpath=" in htaccess


def test_htaccess_combo_uses_os_alone_when_it_distinguishes() -> None:
    # Two binaries differing only by OS: os alone is the minimal unique combo, so neither
    # class (shared) nor arch (shared) appears in the rules.
    version = _version(
        [
            _artifact(
                "apache-x-1.0.0_macos_x86-64.tar.gz",
                "https://m.example/x/macos.tar.gz?action=download",
                classification="binary",
            ),
            _artifact(
                "apache-x-1.0.0_linux_x86-64.tar.gz",
                "https://m.example/x/linux.tar.gz?action=download",
                classification="binary",
            ),
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    assert 'RewriteCond %{QUERY_STRING} "(^|&)os=macos(&|$)"' in htaccess
    assert 'RewriteCond %{QUERY_STRING} "(^|&)os=linux(&|$)"' in htaccess
    assert "class=" not in htaccess
    assert "arch=" not in htaccess


def test_htaccess_combo_needs_os_and_arch_when_neither_alone_distinguishes() -> None:
    # macos/x86-64 shares its OS with the aarch64 build and its arch with the linux build, so
    # its minimal combo is os AND arch together, emitted as two conds on one rule.
    version = _version(
        [
            _artifact(
                "apache-x-1.0.0_macos_x86-64.tar.gz",
                "https://m.example/x/mac-intel.tar.gz?action=download",
                classification="binary",
            ),
            _artifact(
                "apache-x-1.0.0_macos_aarch64.tar.gz",
                "https://m.example/x/mac-arm.tar.gz?action=download",
                classification="binary",
            ),
            _artifact(
                "apache-x-1.0.0_linux_x86-64.tar.gz",
                "https://m.example/x/linux.tar.gz?action=download",
                classification="binary",
            ),
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    assert (
        'RewriteCond %{QUERY_STRING} "(^|&)arch=x86\\-64(&|$)"\n'
        'RewriteCond %{QUERY_STRING} "(^|&)os=macos(&|$)"\n'
        'RewriteRule "^$" "https://m.example/x/mac-intel.tar.gz?action=download" [R=302,NE,L]'
    ) in htaccess


def test_htaccess_ext_distinguishes_format_twins() -> None:
    # Same platform shipped as .tar.gz and .zip: class/os/arch are identical, so ext is the only
    # classifier that separates them, and is the whole minimal combo here.
    version = _version(
        [
            _artifact(
                "apache-x-1.0.0-linux-amd64.tar.gz",
                "https://m.example/x/l.tar.gz?action=download",
                classification="binary",
            ),
            _artifact(
                "apache-x-1.0.0-linux-amd64.zip", "https://m.example/x/l.zip?action=download", classification="binary"
            ),
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    assert 'RewriteCond %{QUERY_STRING} "(^|&)ext=tar\\.gz(&|$)"' in htaccess
    assert 'RewriteCond %{QUERY_STRING} "(^|&)ext=zip(&|$)"' in htaccess
    assert "os=" not in htaccess
    assert "arch=" not in htaccess


def test_htaccess_addresses_signature_checksum_and_sbom_by_file_name() -> None:
    version = _version(
        [
            _artifact(
                "apache-x-1.0.0-src.tgz",
                "https://mirror.example/x/apache-x-1.0.0-src.tgz?action=download",
                signature_path="apache-x-1.0.0-src.tgz.asc",
                signature_url="https://downloads.apache.org/x/apache-x-1.0.0-src.tgz.asc",
                checksum_path="apache-x-1.0.0-src.tgz.sha512",
                checksum_url="https://downloads.apache.org/x/apache-x-1.0.0-src.tgz.sha512",
                sbom_path="apache-x-1.0.0-src.tgz.cdx.json",
                sbom_url="https://downloads.apache.org/x/apache-x-1.0.0-src.tgz.cdx.json",
            )
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    assert "file_name=apache\\-x\\-1\\.0\\.0\\-src\\.tgz\\.asc(&|$)" in htaccess
    assert "file_name=apache\\-x\\-1\\.0\\.0\\-src\\.tgz\\.sha512(&|$)" in htaccess
    assert "file_name=apache\\-x\\-1\\.0\\.0\\-src\\.tgz\\.cdx\\.json(&|$)" in htaccess
    assert "https://downloads.apache.org/x/apache-x-1.0.0-src.tgz.asc" in htaccess
    # RewriteEngine, a file_name pair for the artifact and each of its three companions, plus a
    # subpath pair and an ext pair for the artifact
    assert len(htaccess.splitlines()) == 13


def test_htaccess_omits_companion_files_that_are_absent() -> None:
    # An artifact with a signature but no checksum or SBOM gets file_name rules for just the two.
    version = _version(
        [
            _artifact(
                "apache-x-1.0.0-src.tgz",
                "https://mirror.example/x/apache-x-1.0.0-src.tgz?action=download",
                signature_path="apache-x-1.0.0-src.tgz.asc",
                signature_url="https://downloads.apache.org/x/apache-x-1.0.0-src.tgz.asc",
            )
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    assert ".asc" in htaccess
    assert ".sha512" not in htaccess
    # RewriteEngine, file_name pairs for the artifact and its signature, plus a subpath and an
    # ext pair for the artifact
    assert len(htaccess.splitlines()) == 9


def test_htaccess_skips_artifacts_that_are_not_downloadable() -> None:
    version = _version(
        [
            _artifact("apache-x-1.0.0-src.tgz", "https://mirror.example/x/apache-x-1.0.0-src.tgz?action=download"),
            _artifact("apache-x-1.0.0-unpublished.tgz", None),
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    assert "unpublished" not in htaccess
    # RewriteEngine, then the surviving artifact's file_name, subpath and ext pairs
    assert len(htaccess.splitlines()) == 7


def test_htaccess_is_omitted_when_nothing_is_downloadable() -> None:
    version = _version([_artifact("apache-x-1.0.0-src.tgz", None)])

    assert catalog_site._release_htaccess(version, "x", "x") is None


def test_htaccess_redirect_is_temporary_for_the_mirror_and_permanent_for_the_archive() -> None:
    # closer.lua is a mirror redirector that mustn't be cached as canonical (302); an
    # archive.apache.org URL is a permanent home (301). The status keys off the target host.
    version = _version(
        [
            _artifact(
                "live-1.0.0-src.tgz", "https://www.apache.org/dyn/closer.lua/x/live-1.0.0-src.tgz?action=download"
            ),
            _artifact("old-1.0.0-src.tgz", "https://archive.apache.org/dist/x/old-1.0.0-src.tgz"),
        ]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    rules = [line for line in htaccess.splitlines() if line.startswith("RewriteRule")]
    live = next(line for line in rules if "live-1.0.0" in line)
    old = next(line for line in rules if "old-1.0.0" in line)
    assert "[R=302,NE,L]" in live
    assert "[R=301,NE,L]" in old


def test_htaccess_percent_encodes_and_escapes_the_file_name_value() -> None:
    # The file_name value arrives percent-encoded in the query string (a C# hash as %23),
    # and it's a PCRE pattern, so its dots must not read as wildcards.
    version = _version(
        [_artifact("mono-2.10.0-C#.zip", "https://mirror.example/x/mono-2.10.0-C%23.zip?action=download")]
    )

    htaccess = catalog_site._release_htaccess(version, "x", "x")

    assert htaccess is not None
    assert "file_name=mono\\-2\\.10\\.0\\-C%23\\.zip(&|$)" in htaccess


async def test_write_htaccess_lands_the_dotfile_on_disk(tmp_path) -> None:
    # .htaccess is a dotfile, which StatePath's validating join refuses; the write must drop
    # to the raw OS path. This guards the file reaching disk, not just the generated string.
    version = _version(
        [_artifact("apache-x-1.0.0-src.tgz", "https://mirror.example/x/apache-x-1.0.0-src.tgz?action=download")]
    )

    await catalog_site._write_htaccess(safe.StatePath(tmp_path), version, "x", "x")

    written = tmp_path / ".htaccess"
    assert written.is_file()
    assert written.read_text().startswith("RewriteEngine On")


async def test_write_htaccess_writes_nothing_when_no_artifact_is_downloadable(tmp_path) -> None:
    version = _version([_artifact("apache-x-1.0.0-src.tgz", None)])

    await catalog_site._write_htaccess(safe.StatePath(tmp_path), version, "x", "x")

    assert not (tmp_path / ".htaccess").exists()
