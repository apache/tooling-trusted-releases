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
import subprocess

import cyclonedx.schema
import pytest

import atr.sbom.cyclonedx
import atr.sbom.models.bundle as bundle
import tests.unit.sboms as sboms


def test_validate_cli_reports_stdout_and_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = subprocess.CompletedProcess(
        args=["cyclonedx"],
        returncode=1,
        stdout="Validating JSON BOM...\n",
        stderr="Unhandled exception: System.OutOfMemoryException\n",
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: completed)
    value = bundle.Bundle(
        source_type="json",
        spec_version=cyclonedx.schema.SchemaVersion.V1_6,
        bom=sboms.build({}),
        doc={},
        path=pathlib.Path("bom.cdx.json"),
        text="",
    )

    errors = atr.sbom.cyclonedx.validate_cli(value)

    assert errors == ["Validating JSON BOM...", "Unhandled exception: System.OutOfMemoryException"]
