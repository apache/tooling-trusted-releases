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

from typing import Final

# TODO: Most of the metadata in this module should be moved to the Catalogue Server

FORBIDDEN_PROJECT_CATEGORIES: Final[set[str]] = {
    "retired",
}

# Committees which are allowed by Infra to make releases via GitHub Actions
# These are set via an ldap list.
# TODO: Switch/move this list as appropriate. We do not want to require a code
# release in order to update these configurations.
# GITHUB_AUTOMATED_RELEASE_COMMITTEES: Final[frozenset[str]] = frozenset(
#     {
#         "arrow",
#         "baremaps",
#         "beam",
#         "daffodil",
#         "directory",
#         "logging",
#         "tooling",
#     }
# )

# Committees where commiters are explicitly allowed to promote releases
# For ATR this means that for these committees committers (participants)
# are allowed to start project release.
# Copied from https://github.com/apache/infrastructure-svnauthz/blob/master/gen.py
# Changed roughly once a year.
COMMITTERS_MAY_RELEASE_COMMITTEES: Final[frozenset[str]] = frozenset(
    {
        "bookkeeper",
        "calcite",
        "camel",
        "commons",
        "couchdb",
        "druid",
        "hive",
        "lucene",
        "solr",
        "zookeeper",
    }
)

# Committees which cannot make releases, by policy
STANDING_COMMITTEES: Final[frozenset[str]] = frozenset(
    {
        "attic",
        "comdev",
        "gump",
        "incubator",
        "logodev",
        "petri",
        "whimsy",
    }
)
