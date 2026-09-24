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

"""User-facing UI strings, kept in one place so the same wording is shared between templates and code."""

from typing import Final

# Button labels
REGENERATE_KEYS_BUTTON: Final = "Regenerate KEYS file"
RESET_INACTIVITY_CLOCK_BUTTON: Final = "Reset inactivity clock"
RESOLVE_VOTE_BUTTON: Final = "Resolve vote"
VERIFY_DISTRIBUTION_BUTTON: Final = "Verify a third-party distribution"

# Release information labels
COMMIT_HASH_LABEL: Final = "Commit hash"

# File classification labels
FILE_CLASS_BINARY: Final = "Binary artifact"
FILE_CLASS_DIRECTORY: Final = "Directory"
FILE_CLASS_DISALLOWED: Final = "Disallowed file"
FILE_CLASS_METADATA: Final = "Metadata file"
FILE_CLASS_SBOM: Final = "SBOM"
FILE_CLASS_SOURCE: Final = "Source artifact"

# Dist watcher rule kinds, keyed by DistRuleKind value, in plain terms for the admin who edits them
DIST_RULE_KIND_DESCRIPTIONS: Final[dict[str, str]] = {
    "project_remap": (
        "Sends a release to a named project when its dist path would otherwise land on the wrong key, or on none."
    ),
    "grouping_bucket": (
        "Marks a directory that only groups projects together, for any committee. The project name is read from "
        "the file, not from this directory."
    ),
    "committee_bucket": (
        "A grouping directory scoped to one committee - used when the same word is a real project somewhere else."
    ),
    "excluded_part": "Marks a path segment that never holds a release, so anything beneath it is skipped.",
    "name_build_suffix": (
        "A trailing word to drop from a release name when working out the project, like -incubating or -bin."
    ),
    "airflow_provider_area": (
        "An Airflow directory whose per-provider packages are rolled up into one dated bundle release."
    ),
}
