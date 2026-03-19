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
import datetime
import inspect
import sys
import time
from collections.abc import Awaitable, Callable
from types import ModuleType
from typing import Any

import asfquart.base as base
import pydantic
import quart
import quart.blueprints as blueprints
import quart_rate_limiter as rate_limiter
import quart_schema
import werkzeug.exceptions as exceptions

import atr.blueprints.common as common
import atr.log as log
import atr.web as web

_BLUEPRINT_NAME = "api_blueprint"
_BLUEPRINT = quart.Blueprint(_BLUEPRINT_NAME, __name__, url_prefix="/api")
_routes: list[str] = []


def register(app: base.QuartApp) -> tuple[ModuleType, list[str]]:
    import atr.api as api

    app.register_blueprint(_BLUEPRINT)
    return api, _routes


def typed(func: Callable[..., Awaitable[Any]]) -> web.RouteFunction[Any]:
    """Decorator that derives the URL path from the function's type annotations.

    - Arguments after session are joined with / to make the web path
    - Literal["..."] parameters become literal path segments
    - safe.ProjectName / safe.VersionName parameters are validated via cache/DB
    - pydantic.BaseModel subclass parameters are parsed from the JSON request body
    - dataclass parameters are parsed from the query string
    - str | None parameters create optional URL segments (two routes registered)
    - int, float, str use Quart's built-in type converters
    - HTTP method is POST if a body param is present, GET otherwise
    """
    original = inspect.unwrap(func)
    path, validated_params, literal_params, body_param, _, query_param, optional_params = common.build_api_path(
        original
    )
    method = "POST" if body_param is not None else "GET"
    body_safe_params = common.safe_params_for_type(body_param[1]) if body_param is not None else []
    query_safe_params = common.safe_params_for_type(query_param[1]) if query_param is not None else []

    async def wrapper(*_args: Any, **kwargs: Any) -> Any:
        await common.validate_params(kwargs, validated_params)
        kwargs.update(literal_params)

        if body_param is not None:
            await common.parse_body(body_param, body_safe_params, kwargs)

        if query_param is not None:
            await common.parse_query(query_param, query_safe_params, kwargs)

        start_time_ns = time.perf_counter_ns()
        response = await func(**kwargs)
        end_time_ns = time.perf_counter_ns()
        total_ms = (end_time_ns - start_time_ns) // 1_000_000
        log.performance(f"API {method} {path} {original.__name__} = 0 0 {total_ms}")

        return response

    endpoint = original.__module__.replace(".", "_") + "_" + original.__name__
    wrapper.__name__ = original.__name__
    wrapper.__doc__ = original.__doc__

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

    common.register_route(original, "api", _routes)
    return wrapper


@_BLUEPRINT.before_request
@rate_limiter.rate_limit(500, datetime.timedelta(hours=1))
async def _api_rate_limit() -> None:
    """Set API-wide rate limit"""
    pass


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


def _exempt_blueprint(app: base.QuartApp) -> None:
    csrf = app.extensions.get("csrf")
    if csrf is not None:
        csrf.exempt(_BLUEPRINT)


@_BLUEPRINT.errorhandler(base.ASFQuartException)
async def _handle_asfquart_exception(err: base.ASFQuartException) -> tuple[quart.Response, int]:
    status = getattr(err, "errorcode", 500)
    return _json_error(str(err), status)


@_BLUEPRINT.errorhandler(Exception)
async def _handle_generic_exception(err: Exception) -> tuple[quart.Response, int]:
    return _json_error(str(err), 500)


@_BLUEPRINT.errorhandler(exceptions.HTTPException)
async def _handle_http_exception(err: exceptions.HTTPException) -> tuple[quart.Response, int]:
    return _json_error(err.description or err.name, err.code)


@_BLUEPRINT.errorhandler(exceptions.NotFound)
async def _handle_not_found(err: exceptions.NotFound) -> tuple[quart.Response, int]:
    return _json_error(err.description or err.name, 404)


@_BLUEPRINT.errorhandler(quart_schema.RequestSchemaValidationError)
async def _handle_request_validation(err: quart_schema.RequestSchemaValidationError) -> tuple[quart.Response, int]:
    if not isinstance(err.validation_error, pydantic.ValidationError):
        raise err.validation_error
    verr: pydantic.ValidationError = err.validation_error
    return _json_error("Input validation failed", 400, {"validation_details": verr.errors()})


def _json_error(
    message: str, status_code: int | None, extra: dict[str, Any] | None = None
) -> tuple[quart.Response, int]:
    payload = {"error": message}
    show_traceback = False
    if show_traceback:
        import traceback

        traceback_str = "".join(traceback.format_exception(*sys.exc_info()))
        payload["traceback"] = traceback_str
    if extra is not None:
        payload.update(extra)
    return quart.jsonify(payload), status_code or 500


@_BLUEPRINT.record_once
def _setup(state: blueprints.BlueprintSetupState) -> None:
    if isinstance(state.app, base.QuartApp):
        _exempt_blueprint(state.app)
