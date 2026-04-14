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

import pathlib
import unittest.mock as mock
from types import SimpleNamespace

import atr.models.safe as safe
import atr.storage.writers.announce as announce


def test_committee_downloads_path_podling(tmp_path: pathlib.Path) -> None:
    downloads = safe.StatePath(tmp_path)
    committee = SimpleNamespace(key="myproject", is_podling=True)

    with mock.patch.object(announce.paths, "get_downloads_dir", return_value=downloads):
        result = announce._committee_downloads_path(committee, None)

    assert str(result).endswith("/incubator/myproject")


def test_committee_downloads_path_podling_with_suffix(tmp_path: pathlib.Path) -> None:
    downloads = safe.StatePath(tmp_path)
    committee = SimpleNamespace(key="myproject", is_podling=True)

    with mock.patch.object(announce.paths, "get_downloads_dir", return_value=downloads):
        result = announce._committee_downloads_path(committee, safe.RelPath("apache-myproject-1.0.0"))

    assert str(result).endswith("/incubator/myproject/apache-myproject-1.0.0")


def test_committee_downloads_path_regular(tmp_path: pathlib.Path) -> None:
    downloads = safe.StatePath(tmp_path)
    committee = SimpleNamespace(key="myproject", is_podling=False)

    with mock.patch.object(announce.paths, "get_downloads_dir", return_value=downloads):
        result = announce._committee_downloads_path(committee, safe.RelPath("apache-myproject-1.0.0"))

    assert str(result).endswith("/myproject/apache-myproject-1.0.0")
    assert "/incubator/" not in str(result)
