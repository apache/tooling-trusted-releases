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

import dataclasses
from typing import Literal

import asfquart
import pytest

import atr.blueprints as blueprints
import atr.blueprints.common as common
import atr.form as form
import atr.models.safe as safe
import atr.models.schema as schema
import atr.util as util
import atr.web as web

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


def test_build_api_path_body_param():
    class RequestBody(schema.Strict):
        value: int

    async def route(_session: web.Committer, _page: Literal["submit"], _data: RequestBody) -> str:
        return ""

    _, _, _, body, _, _, _ = common.build_api_path(route)
    assert body is not None
    assert body[0] == "_data"
    assert body[1] is RequestBody


def test_build_api_path_literal_and_safe():
    async def route(
        _session: web.Committer,
        _page: Literal["project"],
        _project_key: safe.ProjectKey,
    ) -> str:
        return ""

    path, validated, literals, body, form_param, query, optional = common.build_api_path(route)
    assert path == "/project/<_project_key>"
    assert validated == [("_project_key", safe.ProjectKey)]
    assert literals == {"_page": "project"}
    assert body is None
    assert form_param is None
    assert query is None
    assert optional == []


def test_build_api_path_optional_param():
    async def route(
        _session: web.Committer,
        _page: Literal["items"],
        _category: str | None = None,
    ) -> str:
        return ""

    path, _, _, _, _, _, optional = common.build_api_path(route)
    assert path == "/items/<_category>"
    assert optional == ["_category"]


def test_build_api_path_optional_safe_type_included_in_validated():
    async def route(
        _session: web.Committer,
        _page: Literal["project"],
        _project_key: safe.ProjectKey | None = None,
    ) -> str:
        return ""

    path, validated, _, _, _, _, optional = common.build_api_path(route)
    assert path == "/project/<_project_key>"
    assert optional == ["_project_key"]
    assert ("_project_key", safe.ProjectKey) in validated


def test_build_api_path_optional_non_safe_type_not_in_validated():
    async def route(
        _session: web.Committer,
        _page: Literal["items"],
        _category: str | None = None,
    ) -> str:
        return ""

    _, validated, _, _, _, _, optional = common.build_api_path(route)
    assert optional == ["_category"]
    assert all(name != "_category" for name, _ in validated)


def test_build_api_path_query_param():
    @dataclasses.dataclass
    class Filters:
        page: int = 1
        search: str = ""

    async def route(_session: web.Committer, _page: Literal["list"], _filters: Filters) -> str:
        return ""

    _, _, _, _, _, query, _ = common.build_api_path(route)
    assert query is not None
    assert query[0] == "_filters"
    assert query[1] is Filters


def test_build_api_path_rejects_duplicate_body():
    class BodyA(schema.Lax):
        x: int

    class BodyB(schema.Lax):
        y: int

    async def route(_session: web.Committer, _a: BodyA, _b: BodyB) -> str:
        return ""

    with pytest.raises(TypeError, match="only one body type is allowed"):
        common.build_api_path(route)


def test_build_api_path_rejects_duplicate_query():
    @dataclasses.dataclass
    class QueryA:
        x: int = 0

    @dataclasses.dataclass
    class QueryB:
        y: int = 0

    async def route(_session: web.Committer, _a: QueryA, _b: QueryB) -> str:
        return ""

    with pytest.raises(TypeError, match="only one query type is allowed"):
        common.build_api_path(route)


def test_build_path_combined_literal_and_safe():
    async def route(
        _session: web.Committer,
        _page: Literal["project"],
        _project_key: safe.ProjectKey,
        _sub: Literal["version"],
        _version_key: safe.VersionKey,
    ) -> str:
        return ""

    path, validated, literals, _, _ = common.build_path(route)
    assert path == "/project/<_project_key>/version/<_version_key>"
    assert validated == [("_project_key", safe.ProjectKey), ("_version_key", safe.VersionKey)]
    assert literals == {"_page": "project", "_sub": "version"}


def test_build_path_form_param():
    class TestForm(form.Form):
        name: str = ""

    async def route(_session: web.Committer, _page: Literal["submit"], _data: TestForm) -> str:
        return ""

    path, _, _, form_param, _ = common.build_path(route)
    assert path == "/submit"
    assert form_param is not None
    assert form_param[0] == "_data"
    assert form_param[1] is TestForm


def test_build_path_int_converter():
    async def route(_session: web.Committer, _page: Literal["item"], _item_id: int) -> str:
        return ""

    path, _, _, _, _ = common.build_path(route)
    assert path == "/item/<int:_item_id>"


def test_build_path_literal_segment():
    async def route(_session: web.Committer, _page: Literal["dashboard"]) -> str:
        return ""

    path, validated, literals, form_param, public = common.build_path(route)
    assert path == "/dashboard"
    assert literals == {"_page": "dashboard"}
    assert validated == []
    assert form_param is None
    assert public is False


def test_build_path_public_session():
    async def route(_session: web.Public, _page: Literal["home"]) -> str:
        return ""

    _, _, _, _, public = common.build_path(route)
    assert public is True


def test_build_path_rejects_bare_str():
    async def route(_session: web.Committer, _name: str) -> str:
        return ""

    with pytest.raises(TypeError, match="unguarded str"):
        common.build_path(route)


def test_build_path_rejects_duplicate_form():
    class FormA(form.Form):
        name: str = ""

    class FormB(form.Form):
        name: str = ""

    async def route(_session: web.Committer, _a: FormA, _b: FormB) -> str:
        return ""

    with pytest.raises(TypeError, match="only one Form is allowed"):
        common.build_path(route)


def test_build_path_rejects_session_not_first():
    async def route(_page: Literal["home"], _session: web.Committer) -> str:
        return ""

    with pytest.raises(TypeError, match="must be first"):
        common.build_path(route)


def test_build_path_rejects_unannotated_param():
    async def route(_session: web.Committer, _thing) -> str:  # type: ignore[reportUnknownParameterType]
        return ""

    with pytest.raises(TypeError, match="no type annotation"):
        common.build_path(route)


def test_build_path_safe_type():
    async def route(_session: web.Committer, _project_key: safe.ProjectKey) -> str:
        return ""

    path, validated, _, _, _ = common.build_path(route)
    assert path == "/<_project_key>"
    assert validated == [("_project_key", safe.ProjectKey)]


def test_safe_params_for_type_empty_for_plain_model():
    class Body(schema.Strict):
        name: str
        count: int

    result = common.safe_params_for_type(Body)
    assert result == []


def test_safe_params_for_type_finds_safe_fields():
    class Body(schema.Strict):
        project_key: safe.ProjectKey
        version_key: safe.VersionKey
        description: str

    result = common.safe_params_for_type(Body)
    assert ("project_key", safe.ProjectKey) in result
    assert ("version_key", safe.VersionKey) in result
    assert len(result) == 2


def test_setup_wrapper_sets_metadata():
    async def index() -> str:
        """Doc string."""
        return ""

    index.__module__ = "atr.get.dashboard"

    async def wrapper() -> str:
        return ""

    endpoint = common.setup_wrapper(wrapper, index, "get_blueprint")
    assert endpoint == "atr_get_dashboard_index"
    assert wrapper.__name__ == "index"
    assert wrapper.__doc__ == "Doc string."
    assert wrapper.__annotations__["endpoint"] == "get_blueprint.atr_get_dashboard_index"


@pytest.mark.asyncio
async def test_validate_params_skips_absent_optional():
    kwargs: dict = {}
    await common.validate_params(kwargs, [("project_key", safe.ProjectKey)])
    assert "project_key" not in kwargs


@pytest.mark.asyncio
async def test_validate_params_validates_present_optional():
    kwargs: dict = {"project_key": "myproject"}
    await common.validate_params(kwargs, [("project_key", safe.ProjectKey)])
    assert isinstance(kwargs["project_key"], safe.ProjectKey)


@pytest.mark.asyncio
async def test_validate_params_raises_on_invalid_optional():
    import asfquart.base as base

    kwargs: dict = {"project_key": "INVALID KEY WITH SPACES"}
    with pytest.raises(base.ASFQuartException):
        await common.validate_params(kwargs, [("project_key", safe.ProjectKey)])
