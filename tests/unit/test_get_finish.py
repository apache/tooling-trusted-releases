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

import atr.get.finish as finish
import atr.util as util


def test_render_publish_step_omits_promotion_for_release_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(util, "svn_publish_target", lambda: util.SvnPublishTarget.RELEASE)
    html = "".join(str(item) for item in finish._render_publish_step())
    assert "dist/release" in html
    assert "dist/atr" not in html
    assert "<a " not in html
