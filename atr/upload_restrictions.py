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

"""The optional restrictions a PMC puts on which GitHub Actions workflow runs can upload to a project."""

import pathlib

import atr.gates as gates
import atr.models.sql as sql
import atr.models.validation as validation


def problem(options: sql.ProjectSecurityOptions, repository: str, workflow_ref: str, ref: str) -> str | None:
    """Say why a workflow run is outside the restrictions, or return None when it is inside them."""
    workflow_paths = options.github_upload_workflow_paths
    if workflow_paths:
        # A workflow ref is the repository, the path of the workflow file, and the ref it was read from
        workflow_path = workflow_ref.removeprefix(f"{repository}/").rsplit("@", 1)[0]
        if workflow_path not in workflow_paths:
            return f"The workflow {workflow_path} is not one of the workflows that can upload to this project"
    ref_type = options.github_upload_ref_type
    if not ref_type:
        return None
    prefix = f"refs/{gates.REF_TYPES[ref_type]}/"
    if not ref.startswith(prefix):
        return f"The run is on {ref}, and only a run on a {ref_type} can upload to this project"
    name = pathlib.PurePosixPath(ref.removeprefix(prefix))
    patterns = options.github_upload_ref_patterns
    if patterns and (not any(name.full_match(pattern) for pattern in patterns)):
        return f"The {ref_type} {name} is not one that can upload to this project"
    return None


def validate(workflow_paths: list[str], ref_type: str, ref_patterns: list[str]) -> None:
    """Raise ValueError unless the values are ones that problem can match against."""
    validation.validate_trusted_publishing_workflow_paths(workflow_paths)
    if ref_type and (ref_type not in gates.REF_TYPES):
        raise ValueError(f"Unsupported ref type: {ref_type}")
    if ref_patterns and (not ref_type):
        raise ValueError("Choose branches or tags for the patterns to apply to.")
    for pattern in ref_patterns:
        if not gates.pattern_supported(pattern):
            raise ValueError(f"Unsupported pattern: {pattern}")
