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

import pydantic

import atr.form as form
import atr.util as util


class StartReleaseForm(form.Form):
    version_key: str = form.label(
        "Version",
        "Enter the version string for this new release."
        " This cannot be changed later, and must be the version of the finished release."
        " ATR generates a unique revision serial number for each voting round,"
        " and you can also set your own tag before a vote starts."
        " Release candidate drafts inactive for 90 days are cleaned up automatically,"
        " with a warning email sent at 80 days.",
    )
    auto_archive_prior: form.Bool = form.label(
        "Auto archive prior release",
        "If set, release of this version will auto-archive the prior release in the cycle",
    )

    @pydantic.field_validator("version_key", mode="after")
    @classmethod
    def validate_version_key(cls, value: str) -> str:
        if error := util.version_key_error(value):
            raise ValueError(error)
        return value
