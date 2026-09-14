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

import json
import pathlib
from typing import Any

import pytest

import atr.sbom.cli as cli
import atr.sbom.utilities as utilities


@pytest.mark.parametrize("references", ["unique", "absent", "repeated"])
async def test_augmentation_preserves_component_positions(references: str, capsys: pytest.CaptureFixture[str]) -> None:
    components: list[dict[str, Any]] = [
        {
            "type": "library",
            "name": "z-existing",
            "version": "1",
            "group": "org.apache.example",
            "supplier": {"name": "Original PMC", "url": ["https://example.apache.org/"]},
        },
        {"type": "library", "name": "a-missing", "version": "1", "group": "org.apache.example"},
        {"type": "library", "name": "a-missing", "version": "1", "group": "org.apache.example"},
    ]
    for index, component in enumerate(components):
        if references != "absent":
            component["bom-ref"] = str(index) if (references == "unique") else "shared"
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": "root", "version": "1", "purl": "pkg:generic/root@1"},
            "supplier": {"name": "Root supplier"},
            "manufacturer": {"name": "Root manufacturer"},
            "timestamp": "2026-09-14T00:00:00Z",
        },
        "components": components,
    }
    bundle = utilities.text_to_bundle(json.dumps(doc), pathlib.Path("bom.cdx.json"))

    cli.command_where(bundle)
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines() if line] == components[1:]

    patch_ops = await utilities.bundle_to_ntia_patch(bundle)
    version, patched = utilities.apply_patch("augment", "00001", bundle, patch_ops)

    assert version == 2
    assert bundle.doc == doc
    assert patched["components"][0] == components[0]
    assert [component["name"] for component in patched["components"]] == ["z-existing", "a-missing", "a-missing"]
    for index in (1, 2):
        assert patched["components"][index] == components[index] | {
            "supplier": {"name": "The Apache Software Foundation", "url": ["https://apache.org/"]}
        }
    augmented = utilities.text_to_bundle(json.dumps(patched), bundle.path)
    assert await utilities.bundle_to_ntia_patch(augmented) == []
