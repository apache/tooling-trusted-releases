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


def test_catalog_handlers_exist_and_import() -> None:
    # Importing atr.admin registers the handlers via the @admin.typed decorator; if the
    # type annotations are malformed the import itself raises, so this both smoke-tests
    # import and confirms the two workspace handlers are present
    assert hasattr(admin_module, "catalog_get")
    assert hasattr(admin_module, "catalog_committee_get")


def test_move_handlers_exist() -> None:
    assert hasattr(admin_module, "catalog_move_get")
    assert hasattr(admin_module, "catalog_move_post")
    assert hasattr(admin_module, "CatalogMoveForm")


def test_rename_handlers_exist() -> None:
    assert hasattr(admin_module, "catalog_rename_get")
    assert hasattr(admin_module, "catalog_rename_post")
    assert hasattr(admin_module, "CatalogRenameForm")


def test_remove_handlers_exist() -> None:
    assert hasattr(admin_module, "catalog_remove_get")
    assert hasattr(admin_module, "catalog_remove_post")
    assert hasattr(admin_module, "CatalogRemoveForm")
