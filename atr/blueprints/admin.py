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
import time
from collections.abc import Awaitable, Callable
from types import ModuleType
from typing import Any, Concatenate, Final, overload

import asfquart.base as base
import asfquart.session
import pydantic
import quart
import quart_schema

import atr.blueprints.common as common
import atr.log as log
import atr.user as user
import atr.web as web

_BLUEPRINT_NAME: Final = "admin_blueprint"
_BLUEPRINT: Final = quart.Blueprint(
    _BLUEPRINT_NAME, __name__, url_prefix="/admin", template_folder="../admin/templates"
)
_routes: list[str] = []


def post(path: str) -> Callable[[web.CommitterRouteFunction[Any]], web.RouteFunction[Any]]:
    def decorator(func: web.CommitterRouteFunction[Any]) -> web.RouteFunction[Any]:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(quart.g.session, *args, **kwargs)

        endpoint = common.setup_wrapper(wrapper, func, _BLUEPRINT_NAME)
        _BLUEPRINT.add_url_rule(path, endpoint=endpoint, view_func=wrapper, methods=["POST"])

        return wrapper

    return decorator


def register(app: base.QuartApp) -> tuple[ModuleType, list[str]]:
    import atr.admin as admin

    app.register_blueprint(_BLUEPRINT)
    return admin, _routes


@overload
def typed[**P, R](func: Callable[Concatenate[web.Committer, P], Awaitable[R]]) -> web.RouteFunction[R]: ...


@overload
def typed[**P, R](func: Callable[Concatenate[web.Public, P], Awaitable[R]]) -> web.RouteFunction[R]: ...  # pyright: ignore[reportOverlappingOverload]


def typed(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that derives the URL path from the function's type annotations.

    - Literal["..."] parameters become literal path segments
    - safe.SafeType subclass parameters are validated from the URL path
    - pydantic.BaseModel subclass parameters are parsed from the JSON request body
    - form.Form subclass parameters are validated from the request body
    - dataclass parameters are parsed from the query string
    - str | None parameters create optional URL segments (two routes registered)
    - int, float use Quart's built-in type converters
    - HTTP method is POST if a body or form param is present, GET otherwise
    """
    path, validated_params, literal_params, body_param, form_param, query_param, optional_params = (
        common.build_api_path(func)
    )
    method = "POST" if ((body_param is not None) or (form_param is not None)) else "GET"
    body_safe_params = common.safe_params_for_type(body_param[1]) if (body_param is not None) else []
    form_safe_params = common.safe_params_for_type(form_param[1]) if (form_param is not None) else []
    query_safe_params = common.safe_params_for_type(query_param[1]) if (query_param is not None) else []

    async def wrapper(*_args: Any, **kwargs: Any) -> Any:
        enhanced_session = await common.authenticate()
        await common.validate_params(kwargs, validated_params)
        kwargs.update(literal_params)

        if body_param is not None:
            await common.parse_body(body_param, body_safe_params, kwargs)

        if form_param is not None:
            form_param_name, form_cls = form_param
            context: dict[str, Any] = {"kwargs": kwargs, "session": enhanced_session}
            try:
                kwargs[form_param_name] = await enhanced_session.form_validate(form_cls, context)
            except pydantic.ValidationError as e:
                return await common.flash_form_error(form_cls, enhanced_session, e)
            if form_safe_params:
                await common.validate_safe_fields(kwargs[form_param_name], form_safe_params, kwargs)

        if query_param is not None:
            await common.parse_query(query_param, query_safe_params, kwargs)

        start_time_ns = time.perf_counter_ns()
        response = await func(enhanced_session, **kwargs)
        end_time_ns = time.perf_counter_ns()
        total_ms = (end_time_ns - start_time_ns) // 1_000_000
        log.performance(f"API {method} {path} {func.__name__} = 0 0 {total_ms}")

        return response

    endpoint = common.setup_wrapper(wrapper, func, _BLUEPRINT_NAME)

    # Replace the original quart request decorators
    if query_param is not None:
        wrapper = quart_schema.validate_querystring(query_param[1])(wrapper)
    if body_param is not None:
        wrapper = quart_schema.validate_request(body_param[1])(wrapper)

    # Examine `func` for quart attributes and re-attach to the wrapped function
    # This makes sure the OpenAPI documentation is preserved
    # Note: we don't update querystring or request as they're processed above using our detected types
    _copy_quart_attributes(func, wrapper)

    # If there are optional params, we need two routes, one with the optional params omitted
    # and one with them all present.
    # AM 26/03/03: This actually only handles the case where there's some required and a single optional, but
    # that's the only case that existed in the original code. Theoretically we could count the optional params and
    # generate the correct number of routes, but that's lot of effort for little gain right now
    _add_url_rules(wrapper, path, endpoint, method, optional_params)

    common.register_route(func, "api", _routes)
    return wrapper


def _add_url_rules(
    wrapper: Callable[..., Any],
    path: str,
    endpoint: str,
    method: str,
    optional_params: list[str],
) -> None:
    """Register URL rules for the wrapper, handling optional path params with a default short route."""
    if optional_params:
        required_segments = [
            seg for seg in path.strip("/").split("/") if not any(seg == f"<{name}>" for name in optional_params)
        ]
        short_path = "/" + "/".join(required_segments)
        defaults = {name: None for name in optional_params}
        _BLUEPRINT.add_url_rule(short_path, endpoint=endpoint, view_func=wrapper, methods=[method], defaults=defaults)
        _BLUEPRINT.add_url_rule(path, endpoint=endpoint + "_full", view_func=wrapper, methods=[method])
    else:
        _BLUEPRINT.add_url_rule(path, endpoint=endpoint, view_func=wrapper, methods=[method])


def _copy_quart_attributes(src: Callable[..., Any], dst: Callable[..., Any]) -> None:
    """Copy quart schema attributes from src to dst to preserve OpenAPI documentation."""
    for attr in common.QUART_ATTRIBUTES:
        if hasattr(src, attr):
            setattr(dst, attr, getattr(src, attr))


@_BLUEPRINT.before_request
async def _check_admin_access() -> None:
    web_session = await asfquart.session.read()
    if web_session is None:
        raise base.ASFQuartException("Not authenticated", errorcode=401)

    if not user.is_admin(web_session.uid):
        raise base.ASFQuartException("You are not authorized to access the admin interface", errorcode=403)

    quart.g.session = web.Committer(web_session)
