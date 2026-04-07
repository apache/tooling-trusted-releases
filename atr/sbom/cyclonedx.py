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

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

from .utilities import get_pointer

if TYPE_CHECKING:
    from collections.abc import Iterable

    import cyclonedx.validation.json

    from . import models


def validate_cli(bundle_value: models.bundle.Bundle) -> list[str] | None:
    args = [
        "cyclonedx",
        "validate",
        "--fail-on-errors",
        "--input-format",
        bundle_value.source_type,
        "--input-file",
        bundle_value.path.as_posix(),
    ]
    env = os.environ.copy()
    env["DOTNET_GCHeapHardLimitSOH"] = "0x03000000"
    env["DOTNET_GCHeapHardLimitLOH"] = "0x01000000"
    env["DOTNET_GCHeapHardLimitPOH"] = "0x01000000"
    env["DOTNET_GCHeapCount"] = "1"
    proc = subprocess.run(
        args,
        text=True,
        capture_output=True,
        env=env,
    )
    if proc.returncode != 0:
        err = proc.stdout.strip() or proc.stderr.strip() or "cyclonedx failed"
        return err.splitlines()
    return None


def validate_py(
    bundle_value: models.bundle.Bundle,
) -> Iterable[cyclonedx.validation.json.JsonValidationError] | None:
    import cyclonedx.exception
    import cyclonedx.schema
    import cyclonedx.validation.json

    json_sv = get_pointer(bundle_value.doc, "/specVersion")
    schema_version = cyclonedx.schema.SchemaVersion.V1_6
    if isinstance(json_sv, str):
        schema_version = cyclonedx.schema.SchemaVersion.from_version(json_sv)
    try:
        validator = cyclonedx.validation.json.JsonStrictValidator(schema_version)
        errors = validator.validate_str(bundle_value.text, all_errors=True)
    except cyclonedx.exception.MissingOptionalDependencyException:
        # Placeholder, just in case we want to handle this somehow
        raise
    if isinstance(errors, cyclonedx.validation.json.JsonValidationError):
        # The VSC type checker doesn't think this can happen
        # But pyright does
        return [errors]
    return errors
