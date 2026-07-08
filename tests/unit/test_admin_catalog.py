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

import atr.admin as admin_module
import atr.shared.catalogue_diff as catalogue_diff
import atr.shared.catalogue_rows as catalogue_rows


def _release_row(key: str, project: str, version: str) -> catalogue_rows.ReleaseRow:
    return catalogue_rows.ReleaseRow.model_validate(
        {"key": key, "project_key": project, "version": version, "phase": "release", "created": "2020-01-01"}
    )


def test_import_preview_lists_conflicts_and_repoints() -> None:
    diff = catalogue_diff.CatalogueDiff(
        release_repoints=[
            catalogue_diff.ReleaseRepoint(
                key="alpha-one-4.0.0",
                from_project="alpha-one",
                to_project="alpha-two",
                row=_release_row("alpha-one-4.0.0", "alpha-two", "4.0.0"),
            )
        ],
        conflicts=[catalogue_diff.Conflict(table="artifacts", key="c.tar.gz", reason="unresolvable identity")],
    )
    html = str(admin_module._catalog_import_preview(diff))
    assert "unresolvable identity" in html
    assert "alpha-one-4.0.0 to alpha-two" in html


def test_import_preview_warns_about_deletions_and_empty_levels() -> None:
    diff = catalogue_diff.CatalogueDiff(
        release_deletions=["alpha-one-1.0.0"],
        artifact_deletions=[("alpha-one", "1.0.0", "a.tar.gz")],
        warnings=["artifacts.csv was not uploaded, so 1 artifacts will be deleted and not restored."],
    )
    html = str(admin_module._catalog_import_preview(diff))
    assert "artifacts.csv was not uploaded" in html
    assert "release alpha-one-1.0.0" in html
    assert "anything unspecified to be deleted" in html
