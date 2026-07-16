#!/usr/bin/env python3

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

"""Check that the installed SBOM toolchain agrees on a CycloneDX spec version.

syft generates SBOMs, the CycloneDX CLI validates them, and cyclonedx-python-lib parses
and re-serialises them. If syft or the lib can emit a spec version the CLI cannot
validate, the platform produces files it then rejects. This is meant to run inside the
built image, where all three are present.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from typing import Final

import cyclonedx.exception.output as exc_output
import cyclonedx.model.bom as bom
import cyclonedx.output as output
import cyclonedx.schema as schema

_ISSUE_REF: Final = "issue #1403"
_PROBE_MARKER: Final = "placeholder.txt"


def cli_accepts(cyclonedx_bin: str, document: str) -> bool:
    with tempfile.NamedTemporaryFile("w", suffix=".cdx.json") as handle:
        handle.write(document)
        handle.flush()
        proc = subprocess.run(
            [cyclonedx_bin, "validate", "--fail-on-errors", "--input-format", "json", "--input-file", handle.name],
            text=True,
            capture_output=True,
            check=False,
        )
    return proc.returncode == 0


def cli_supported_versions(cyclonedx_bin: str) -> list[schema.SchemaVersion]:
    supported = []
    for version in schema.SchemaVersion:
        try:
            rendered = output.make_outputter(
                bom=bom.Bom(), output_format=schema.OutputFormat.JSON, schema_version=version
            ).output_as_string()
        except exc_output.FormatNotSupportedException:
            # JSON only exists from spec 1.2 onward, so the older versions never apply here
            continue
        if cli_accepts(cyclonedx_bin, rendered):
            supported.append(version)
    return supported


def main() -> int:
    syft_bin = shutil.which("syft")
    cyclonedx_bin = shutil.which("cyclonedx")
    require_tools = "--require-tools" in sys.argv[1:]
    if (syft_bin is None) or (cyclonedx_bin is None):
        missing = f"syft={syft_bin} cyclonedx={cyclonedx_bin}"
        if require_tools:
            print(f"FAIL: SBOM tools not both present ({missing})", file=sys.stderr)
            return 1
        print(f"SKIP: SBOM tools not both present ({missing})")
        return 0

    cli_versions = cli_supported_versions(cyclonedx_bin)
    lib_ceiling = max(schema.SchemaVersion)
    syft_spec = syft_default_spec(syft_bin)
    syft_version = schema.SchemaVersion.from_version(syft_spec)

    print(f"CLI validates:      {sorted(v.to_version() for v in cli_versions)}")
    print(f"lib can emit up to: {lib_ceiling.to_version()}")
    print(f"syft emits:         {syft_spec}")

    problems = []
    if syft_version not in cli_versions:
        problems.append(f"syft emits {syft_spec} but the CLI cannot validate it (syft to CLI)")
    if lib_ceiling not in cli_versions:
        problems.append(f"lib can emit {lib_ceiling.to_version()} but the CLI cannot validate it (lib to CLI)")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem} - see {_ISSUE_REF}", file=sys.stderr)
        return 1

    print("PASS: syft, the CLI, and cyclonedx-python-lib agree on a CycloneDX spec")
    return 0


def syft_default_spec(syft_bin: str) -> str:
    with tempfile.TemporaryDirectory() as work:
        with open(f"{work}/{_PROBE_MARKER}", "w", encoding="utf-8") as handle:
            handle.write("placeholder\n")
        proc = subprocess.run([syft_bin, work, "-o", "cyclonedx-json"], text=True, capture_output=True, check=True)
    return json.loads(proc.stdout).get("specVersion", "")


if __name__ == "__main__":
    sys.exit(main())
