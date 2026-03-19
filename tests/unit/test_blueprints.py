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

import asfquart
import pytest

import atr.blueprints as blueprints
import atr.util as util

_TESTED_BLUEPRINTS = frozenset({"get_blueprint", "post_blueprint", "admin_blueprint"})


@pytest.mark.asyncio
async def test_all_routes_support_url_construction(monkeypatch):
    # Prevent writing routes.json to a real directory
    monkeypatch.setattr("atr.blueprints._export_routes", lambda _: None)
    # We don't need a functional .APP for this test but we do need it reset afterwards
    monkeypatch.setattr("asfquart.APP", None)

    app = asfquart.construct("test")
    blueprints.register(app)

    failures: list[str] = []

    async with app.test_request_context("/"):
        for rule in app.url_map.iter_rules():
            blueprint_name = rule.endpoint.rsplit(".", 1)[0] if "." in rule.endpoint else None
            if blueprint_name not in _TESTED_BLUEPRINTS:
                continue

            view_func = app.view_functions[rule.endpoint]

            # Build dummy kwargs so url_for can construct the URL
            kwargs = {}
            for arg in rule.arguments:
                converter = rule._converters.get(arg)
                if converter is not None and type(converter).__name__ == "IntegerConverter":
                    kwargs[arg] = 1
                else:
                    kwargs[arg] = "test"

            try:
                util.as_url(view_func, **kwargs)
            except Exception as e:
                failures.append(f"{rule.endpoint} ({rule.rule}): {e}")

    if failures:
        raise AssertionError("Routes incompatible with as_url:\n" + "\n".join(failures))
