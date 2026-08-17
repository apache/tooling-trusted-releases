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

import pytest

import atr.config as config


def test_validate_svn_publish_production_accepts_both_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "is_production_mode", lambda: True)
    config._validate_svn_publish(_conf(monkeypatch, "https://dist.apache.org/repos/dist/atr"))
    config._validate_svn_publish(_conf(monkeypatch, "svn://127.0.0.1:3690/atr-dev-publish"))


def test_validate_svn_publish_rejects_missing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "is_production_mode", lambda: False)
    with pytest.raises(RuntimeError):
        config._validate_svn_publish(_conf(monkeypatch, ""))
    conf = _conf(monkeypatch, "file:///opt/atr/state/dev-svn-repo")
    monkeypatch.setattr(conf, "SVN_TOKEN", "", raising=False)
    with pytest.raises(RuntimeError):
        config._validate_svn_publish(conf)
    with pytest.raises(RuntimeError):
        config._validate_svn_publish(_conf(monkeypatch, "svn://svn.example.org/atr-dev-publish"))


def test_validate_svn_publish_requires_local_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "is_production_mode", lambda: False)
    config._validate_svn_publish(_conf(monkeypatch, "file:///opt/atr/state/dev-svn-repo"))
    config._validate_svn_publish(_conf(monkeypatch, "svn://127.0.0.1:3690/atr-dev-publish"))
    with pytest.raises(RuntimeError):
        config._validate_svn_publish(_conf(monkeypatch, "https://dist.apache.org/repos/dist/atr"))


def test_validate_svn_publish_requires_matching_dist_area(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "is_production_mode", lambda: True)
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", "https://dist.apache.org/repos/dist/release")
    config._validate_svn_publish(_conf(monkeypatch, "https://svn-internal.apache.org/x/repos/dist/release"))
    with pytest.raises(RuntimeError):
        config._validate_svn_publish(_conf(monkeypatch, "https://dist.apache.org/repos/dist/atr"))


def _conf(monkeypatch: pytest.MonkeyPatch, url: str) -> type[config.AppConfig]:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", url, raising=False)
    monkeypatch.setattr(config.get(), "SVN_TOKEN", "x", raising=False)
    return config.get()
