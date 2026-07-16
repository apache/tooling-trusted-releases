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

from typing import Any

import cyclonedx.model.bom as cdx_bom

_BASE: dict[str, Any] = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.6",
    "version": 1,
}


def build(payload: dict[str, Any]) -> cdx_bom.Bom:
    """Build a CycloneDX BOM from the given document fields."""
    built = cdx_bom.Bom.from_json(data=_BASE | payload)
    if built is None:
        raise ValueError("Could not build the BOM under test")
    return built


def with_components(*components: dict[str, Any]) -> cdx_bom.Bom:
    """Build a CycloneDX BOM declaring the given components."""
    return build({"components": list(components)})
