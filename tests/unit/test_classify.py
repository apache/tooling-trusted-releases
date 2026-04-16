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


import json

import atr.classify as classify
import atr.models.safe as safe


async def test_archive_defaults_to_source():
    path = safe.RelPath("apache-widget-1.0.tar.gz")
    assert (await classify.classify(path)) == classify.FileType.SOURCE


async def test_archive_tgz_defaults_to_source():
    path = safe.RelPath("apache-widget-1.0.tgz")
    assert (await classify.classify(path)) == classify.FileType.SOURCE


async def test_archive_zip_defaults_to_source():
    path = safe.RelPath("apache-widget-1.0.zip")
    assert (await classify.classify(path)) == classify.FileType.SOURCE


async def test_binary_matcher_classifies_as_binary(tmp_path):
    path = safe.RelPath("maven-mvnd-1.0.4-windows-amd64.zip")
    result = await classify.classify(path, base_path=safe.StatePath(tmp_path), binary_matcher=_matches_windows)
    assert result == classify.FileType.BINARY


async def test_binary_matcher_does_not_override_source_matcher(tmp_path):
    path = safe.RelPath("apache-widget-1.0.tar.gz")
    result = await classify.classify(
        path, base_path=safe.StatePath(tmp_path), source_matcher=_always_true, binary_matcher=_always_true
    )
    assert result == classify.FileType.SOURCE


async def test_binary_matcher_wins_over_stem_heuristic(tmp_path):
    path = safe.RelPath("apache-widget-1.0-src.tar.gz")
    result = await classify.classify(path, base_path=safe.StatePath(tmp_path), binary_matcher=_always_true)
    assert result == classify.FileType.BINARY


async def test_binary_stem_heuristic():
    path = safe.RelPath("apache-widget-1.0-binary.zip")
    assert (await classify.classify(path)) == classify.FileType.BINARY


def test_counts_docs_only():
    assert classify.classify_from_counts(0, 0, 1) == classify.FileType.DOCS


def test_counts_docs_with_binary():
    assert classify.classify_from_counts(0, 1, 1) == classify.FileType.BINARY


def test_counts_docs_with_source():
    assert classify.classify_from_counts(1, 0, 1) == classify.FileType.SOURCE


async def test_disallowed_files_detected():
    path = safe.RelPath("desktop.ini")
    assert (await classify.classify(path)) == classify.FileType.DISALLOWED


async def test_docs_stem_heuristic():
    path = safe.RelPath("apache-widget-1.0-docs.tar.gz")
    assert (await classify.classify(path)) == classify.FileType.DOCS


async def test_docs_stem_heuristic_javadoc():
    path = safe.RelPath("apache-widget-1.0-javadoc.zip")
    assert (await classify.classify(path)) == classify.FileType.DOCS


async def test_docs_stem_heuristic_site():
    path = safe.RelPath("apache-widget-1.0-site.tar.gz")
    assert (await classify.classify(path)) == classify.FileType.DOCS


async def test_jar_defaults_to_binary():
    path = safe.RelPath("apache-widget-1.0.jar")
    assert (await classify.classify(path)) == classify.FileType.BINARY


def test_matchers_from_policy_builds_both(tmp_path):
    source_matcher, binary_matcher = classify.matchers_from_policy(["*-src.*"], ["*-bin.*"], safe.StatePath(tmp_path))
    assert source_matcher is not None
    assert binary_matcher is not None


def test_matchers_from_policy_empty(tmp_path):
    source_matcher, binary_matcher = classify.matchers_from_policy([], [], safe.StatePath(tmp_path))
    assert source_matcher is None
    assert binary_matcher is None


async def test_metadata_suffix_detected():
    path = safe.RelPath("apache-widget-1.0.tar.gz.sha512")
    assert (await classify.classify(path)) == classify.FileType.METADATA


async def test_npm_content_marker_classifies_as_binary(tmp_path):
    path = safe.RelPath("apache-widget-1.0.tgz")
    cache_dir = _build_npm_pack_layout(tmp_path, name="apache-widget", version="1.0.0")
    result = await classify.classify(path, archive_cache_dir=safe.StatePath(cache_dir))
    assert result == classify.FileType.BINARY


async def test_npm_content_marker_ignored_when_layout_invalid(tmp_path):
    path = safe.RelPath("apache-widget-1.0.tgz")
    cache_dir = _build_npm_pack_layout(tmp_path, name="apache-widget", version="1.0.0")
    (cache_dir / "extra").mkdir()
    result = await classify.classify(path, archive_cache_dir=safe.StatePath(cache_dir))
    assert result == classify.FileType.SOURCE


async def test_npm_content_marker_ignored_when_package_dir_is_symlink(tmp_path):
    path = safe.RelPath("apache-widget-1.0.tgz")
    cache_dir = tmp_path / "cache"
    real_dir = cache_dir / "real_pkg"
    real_dir.mkdir(parents=True)
    (real_dir / "package.json").write_text(json.dumps({"name": "widget", "version": "1.0.0"}))
    (cache_dir / "package").symlink_to(real_dir, target_is_directory=True)
    result = await classify.classify(path, archive_cache_dir=safe.StatePath(cache_dir))
    assert result == classify.FileType.SOURCE


async def test_npm_content_marker_ignored_when_package_json_is_symlink(tmp_path):
    path = safe.RelPath("apache-widget-1.0.tgz")
    cache_dir = _build_npm_pack_layout(tmp_path, name="apache-widget", version="1.0.0")
    package_json = cache_dir / "package" / "package.json"
    shadow = cache_dir / "package" / "metadata.json"
    package_json.rename(shadow)
    package_json.symlink_to(shadow)
    result = await classify.classify(path, archive_cache_dir=safe.StatePath(cache_dir))
    assert result == classify.FileType.SOURCE


async def test_npm_content_marker_ignored_when_package_json_malformed(tmp_path):
    path = safe.RelPath("apache-widget-1.0.tgz")
    cache_dir = tmp_path / "cache"
    package_dir = cache_dir / "package"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text("{ not json")
    result = await classify.classify(path, archive_cache_dir=safe.StatePath(cache_dir))
    assert result == classify.FileType.SOURCE


async def test_source_matcher_wins_over_npm_content_marker(tmp_path):
    path = safe.RelPath("apache-widget-1.0.tgz")
    cache_dir = _build_npm_pack_layout(tmp_path, name="apache-widget", version="1.0.0")
    result = await classify.classify(
        path,
        base_path=safe.StatePath(tmp_path),
        source_matcher=_always_true,
        archive_cache_dir=safe.StatePath(cache_dir),
    )
    assert result == classify.FileType.SOURCE


async def test_source_matcher_wins_over_stem_binary(tmp_path):
    path = safe.RelPath("apache-widget-1.0-bin.tar.gz")
    result = await classify.classify(path, base_path=safe.StatePath(tmp_path), source_matcher=_always_true)
    assert result == classify.FileType.SOURCE


async def test_source_stem_heuristic():
    path = safe.RelPath("apache-widget-1.0-source.tar.gz")
    assert (await classify.classify(path)) == classify.FileType.SOURCE


async def test_stem_heuristics_are_fallback_after_policy(tmp_path):
    path = safe.RelPath("apache-widget-1.0-src.tar.gz")
    result = await classify.classify(
        path, base_path=safe.StatePath(tmp_path), source_matcher=_always_false, binary_matcher=_always_false
    )
    assert result == classify.FileType.SOURCE


def _always_false(_path: str) -> bool:
    return False


def _always_true(_path: str) -> bool:
    return True


def _build_npm_pack_layout(tmp_path, name: str, version: str):
    cache_dir = tmp_path / "cache"
    package_dir = cache_dir / "package"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(json.dumps({"name": name, "version": version}))
    return cache_dir


def _matches_windows(path: str) -> bool:
    return "windows" in path
