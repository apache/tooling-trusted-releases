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

from . import schema


class RatFileEntry(schema.Lax):
    """Entry for a file with license issues."""

    name: str = schema.default("")
    license: str = schema.default("")


class Rat(schema.Lax):
    """Data from a RAT license check, stored in CheckResult.data."""

    valid: bool = schema.default(False)
    message: str = schema.default("")
    total_files: int = schema.default(0)
    approved_licenses: int = schema.default(0)
    unapproved_licenses: int = schema.default(0)
    unknown_licenses: int = schema.default(0)
    errors: list[str] = schema.factory(list)
    excludes_source: str = schema.default("unknown")
    extended_std_applied: bool = schema.default(False)
    warning: str | None = schema.default(None)
    unapproved_files: list[RatFileEntry] = schema.factory(list)
    unknown_license_files: list[RatFileEntry] = schema.factory(list)
    command: list[str] = schema.factory(list)
