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
import asyncio
import contextlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import Final

import aiofiles.os
import exarch

import atr.config as config
import atr.log as log
import atr.models.safe as safe

MAX_ARCHIVE_MEMBERS: Final[int] = 100_000

# Canonical form for archive-suffix aliases. detection.py uses this for
# quarantine dedup; exarch_compatible_path rewrites filenames with it so
# exarch accepts them.
ARCHIVE_NORMALISED_SUFFIXES: Final[dict[str, str]] = {
    ".tgz": ".tar.gz",
    ".jar": ".zip",
    ".war": ".zip",
    ".whl": ".zip",
    ".nar": ".zip",
    ".nbm": ".zip",
    ".vsix": ".zip",
    ".apk": ".zip",
}

# Zip-family aliases exarch doesn't recognise by extension. Derived from
# ARCHIVE_NORMALISED_SUFFIXES so adding a new .zip-typed alias picks up the
# hardlink rewrite automatically. .tgz is excluded because exarch handles
# it natively.
_EXARCH_REWRITE_SUFFIXES: Final[frozenset[str]] = frozenset(
    alias for alias, canonical in ARCHIVE_NORMALISED_SUFFIXES.items() if canonical == ".zip"
)


class ExtractionError(Exception):
    pass


def extract(
    archive_path: safe.StatePath,
    extract_dir: str,
    max_size: int,
    chunk_size: int = 4096,
    track_files: bool | set[str] = False,
) -> tuple[int, list[str]]:
    # chunk_size retained for signature stability; exarch manages buffering internally.
    del chunk_size
    log.info(f"Extracting {archive_path} to {extract_dir}")

    cfg = _build_extraction_config(max_size)
    extracted_paths: list[str] = []

    with exarch_compatible_path(archive_path) as exarch_path:
        if isinstance(track_files, set) and track_files:
            try:
                manifest = exarch.list_archive(exarch_path, cfg)
            except Exception as exc:
                raise ExtractionError(f"Failed to list archive: {exc}") from exc
            for entry in manifest.entries:
                if os.path.basename(entry.path) in track_files:
                    extracted_paths.append(entry.path)

        try:
            report = exarch.extract_archive(exarch_path, extract_dir, cfg)
        except exarch.QuotaExceededError as exc:
            raise ExtractionError(f"Extraction exceeded size limit of {max_size} bytes: {exc}") from exc
        except Exception as exc:
            raise ExtractionError(f"Failed to extract archive: {exc}") from exc

    return report.bytes_written, extracted_paths


def extraction_config() -> exarch.SecurityConfig:
    """Shared secure extraction config for Apache release archives.

    Permits symlinks (that remain within the extraction root), world-writable files,
    and `.env` files — all of which appear legitimately in real Apache releases.
    Rejects hardlinks, absolute paths, and enforces size/depth/compression quotas.
    """
    return _build_extraction_config(config.get().MAX_EXTRACT_SIZE)


async def list_archive(file_path: safe.StatePath) -> list[str] | None:
    """Attempt to list contents of supported archive files."""
    if not await aiofiles.os.path.isfile(file_path):
        return None
    if not file_path.name.endswith((".tar.gz", ".tgz", ".zip")):
        return None

    def _read_archive() -> list[str] | None:
        with contextlib.suppress(Exception):
            manifest = exarch.list_archive(str(file_path), extraction_config())
            return sorted(entry.path for entry in manifest.entries)
        return None

    return await asyncio.to_thread(_read_archive)


@contextlib.contextmanager
def exarch_compatible_path(archive_path: safe.StatePath) -> Iterator[str]:
    # exarch dispatches on extension and doesn't recognise .jar/.war/.whl.
    # For those, hardlink under a .zip name in a tempdir and hand exarch that.
    source = archive_path.path
    lower_name = source.name.lower()
    alias = next((s for s in _EXARCH_REWRITE_SUFFIXES if lower_name.endswith(s)), None)
    if alias is None:
        yield str(source)
        return
    canonical = ARCHIVE_NORMALISED_SUFFIXES[alias]
    # Hardlinks can't cross filesystems, so the tempdir has to sit next to
    # the source. /tmp can be a separate tmpfs partition which would
    # fail with EXDEV.
    tmp_dir = tempfile.mkdtemp(prefix="atr-exarch-", dir=str(source.parent))
    try:
        link_path = os.path.join(tmp_dir, f"archive{canonical}")
        os.link(source, link_path)
        yield link_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _build_extraction_config(max_extract_size: int) -> exarch.SecurityConfig:
    cfg = (
        exarch.SecurityConfig()
        .max_file_size(max_extract_size)
        .max_total_size(max_extract_size)
        .max_file_count(MAX_ARCHIVE_MEMBERS)
        .max_compression_ratio(100.0)
        .max_path_depth(32)
        # Escaping the root is still disallowed by exarch even when symlinks are allowed
        .allow_symlinks(True)
        .allow_hardlinks(False)
        .allow_absolute_paths(False)
        # Too many Apache archives ship world-writable files for us to reject them;
        # we chmod after extraction anyway via util.chmod_files.
        .allow_world_writable(True)
    )
    banned = cfg.banned_path_components
    cfg.banned_path_components = [component for component in banned if component.lower() != ".env"]
    return cfg
