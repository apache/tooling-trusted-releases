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

import jinja2
import pytest
import quart

import atr.template as template


@pytest.mark.parametrize(
    ("render", "source"),
    [
        (template.render_sync, "example.html"),
        (template.render_string_sync, "{{ request.path }} {{ value }}"),
    ],
)
async def test_render_sync_populates_context(render, source):
    app = quart.Quart(__name__)
    app.jinja_environment = template.SyncEnvironment
    app.jinja_loader = jinja2.DictLoader({"example.html": "{{ request.path }} {{ value }}"})
    async with app.test_request_context("/example"):
        assert await render(source, value="alpha") == "/example alpha"
