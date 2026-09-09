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

import html.parser as parser
import urllib.parse

import atr.catalog_site as catalog_site
import atr.models.api as api
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql

_ARTIFACT = 'source "a" & #%.zip'
_SBOM_URL = "https://downloads.apache.org/example/sbom.json"
_COMMITTEE = sql.Committee(key="example", name="Example", catalog_reviewed=True)
_PROJECT = sql.Project(key="example", name="Apache Example")


class Tags(parser.HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def _row(version, artifact, **changes) -> results.SBOMHeatmapRow:
    return results.SBOMHeatmapRow(
        **{
            "key": "pkg:npm/example",
            "purl": f"pkg:npm/example@{version}",
            "source_purls": [f"pkg:npm/example@{version}"],
            "name": "example",
            "ecosystem": "npm",
            "version": version,
            "artifacts": [artifact],
            "advisory_status": "none",
        }
        | changes
    )


def _snapshot() -> results.SBOMHeatmap:
    return results.SBOMHeatmap(
        analysis_version=1,
        generated_at="2026-09-08T15:51:00+00:00",
        sboms=[
            results.SBOMHeatmapSbom(artifact_path=_ARTIFACT, sbom_url=_SBOM_URL, sha256="a" * 64),
            results.SBOMHeatmapSbom(artifact_path="binary.zip", sbom_url=_SBOM_URL),
            results.SBOMHeatmapSbom(artifact_path="bad.zip", sbom_url=_SBOM_URL, error="Invalid <component>"),
        ],
        rows=[
            _row("1", _ARTIFACT, advisory_status="found", advisories=["GHSA-example"], health=0.8, health_inputs=4),
            _row(
                "2", "binary.zip", health=0.0, health_inputs=4, maintained=0, archived=False, criticality=0.0, risk=0.0
            ),
            _row(
                None,
                _ARTIFACT,
                name="<script>alert(1)</script>",
                advisory_status="version_unknown",
                repository_url="javascript:alert(1)",
                artifacts=[_ARTIFACT, "binary.zip"],
                health_inputs=2,
            ),
            _row("3", _ARTIFACT, advisory_status="failed"),
            _row("4", _ARTIFACT, advisory_status="not_found"),
        ],
        sources={"Provider": "https://example.org/data", "Bad URL": "javascript:alert(2)"},
        attribution="Example data attribution",
    )


def _version() -> api.CatalogVersion:
    artifacts = [
        api.CatalogArtifact(
            artifact_path=path,
            classification=None,
            signature_path=None,
            checksum_path=None,
            sbom_path="sbom.json",
            key_fingerprint=None,
            svn_revision=None,
            managed=False,
            dated=None,
            downloadable=False,
            artifact_url=None,
            signature_url=None,
            checksum_url=None,
            sbom_url=_SBOM_URL,
        )
        for path in (_ARTIFACT, "binary.zip", "bad.zip", "unanalysed.zip")
    ]
    return api.CatalogVersion(
        version=safe.VersionKey("1.0"),
        status="released",
        released=None,
        svn_revision=None,
        managed=False,
        cycle=None,
        artifacts=artifacts,
    )


async def test_empty_heatmap_still_explains_input_errors(tmp_path) -> None:
    snapshot = _snapshot().model_copy(update={"rows": []})
    await catalog_site._write_release(safe.StatePath(tmp_path), _COMMITTEE, _PROJECT, _version(), "../", None, snapshot)

    html = (tmp_path / "1.0/heatmap.html").read_text()
    assert '<tbody id="rows"></tbody>' in html
    assert "Invalid &lt;component&gt;" in html
    assert "Download analysis JSON" in html


async def test_heatmap_renders_versions_evidence_and_safe_links(tmp_path) -> None:
    snapshot = _snapshot()
    await catalog_site._write_release(safe.StatePath(tmp_path), _COMMITTEE, _PROJECT, _version(), "../", None, snapshot)

    html = (tmp_path / "1.0/heatmap.html").read_text()
    tags = Tags(html).elements
    options = [attrs["value"] for tag, attrs in tags if tag == "option"]
    assert options == ["", _ARTIFACT, "binary.zip", "bad.zip"]
    assert '<tbody id="rows"></tbody>' in html
    assert "Invalid &lt;component&gt;" in html
    assert "<script>alert(1)</script>" not in html
    links = [attrs["href"] for tag, attrs in tags if tag == "a"]
    assert not any(link.startswith("javascript:") for link in links)
    assert "https://example.org/data" in links
    assert "Alpha-Omega dependency heatmap" in html
    assert "assets/js/src/heatmap-table.js" in html
    assert "assets/css/heatmap.css" in html
    assert "assets/licenses/LICENSE-Alpha-Omega-Heatmap.txt" in html
    assert "Example data attribution" in html
    assert results.SBOMHeatmap.model_validate_json((tmp_path / "1.0/heatmap.json").read_text()) == snapshot

    release = Tags((tmp_path / "1.0/index.html").read_text()).elements
    links = [attrs["href"] for tag, attrs in release if tag == "a"]
    assert f"heatmap.html#sbom={urllib.parse.quote(_ARTIFACT)}" in links
    assert "heatmap.html#sbom=bad.zip" in links
    assert "heatmap.html#sbom=unanalysed.zip" not in links

    await catalog_site._write_assets(safe.StatePath(tmp_path))
    assert (tmp_path / "assets/css/heatmap.css").is_file()
    assert 'fetch("heatmap.json")' in (tmp_path / "assets/js/src/heatmap-table.js").read_text()
    assert (
        "Copyright (c) 2026 Alpha-Omega" in (tmp_path / "assets/licenses/LICENSE-Alpha-Omega-Heatmap.txt").read_text()
    )


async def test_release_without_heatmap_removes_old_files_and_links(tmp_path) -> None:
    directory = safe.StatePath(tmp_path)
    version = _version()
    await catalog_site._write_release(directory, _COMMITTEE, _PROJECT, version, "../", None, _snapshot())
    await catalog_site._write_release(directory, _COMMITTEE, _PROJECT, version, "../", None)

    html = (tmp_path / "1.0/index.html").read_text()
    assert "heatmap.html" not in html
    assert not (tmp_path / "1.0/heatmap.html").exists()
    assert not (tmp_path / "1.0/heatmap.json").exists()
    assert (tmp_path / "1.0/artifacts.json").is_file()
    await catalog_site._write_release(directory, _COMMITTEE, _PROJECT, version, "../", None)
