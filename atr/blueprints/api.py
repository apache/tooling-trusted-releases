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
import datetime
import inspect
import sys
import time
from collections.abc import Callable
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

_BLUEPRINT_NAME = "api_blueprint"
_BLUEPRINT = quart.Blueprint(_BLUEPRINT_NAME, __name__, url_prefix="/api")
_routes: list[str] = []
_QUART_ATTRIBUTES = [
    quart_schema.validation.QUART_SCHEMA_HEADERS_ATTRIBUTE,
    quart_schema.validation.QUART_SCHEMA_RESPONSE_ATTRIBUTE,
    quart_schema.openapi.QUART_SCHEMA_SECURITY_ATTRIBUTE,
    quart_schema.openapi.QUART_SCHEMA_TAG_ATTRIBUTE,
    quart_schema.openapi.QUART_SCHEMA_HIDDEN_ATTRIBUTE,
    quart_schema.openapi.QUART_SCHEMA_DEPRECATED_ATTRIBUTE,
    quart_schema.openapi.QUART_SCHEMA_OPERATION_ID_ATTRIBUTE,
]


def register(app: base.QuartApp) -> tuple[ModuleType, list[str]]:
    import atr.api as api

    app.register_blueprint(_BLUEPRINT)
    return api, _routes


def typed(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that derives the URL path from the function's type annotations.

    - Literal["..."] parameters become literal path segments
    - safe.ProjectName / safe.VersionName parameters are validated via cache/DB
    - pydantic.BaseModel subclass parameters are parsed from the JSON request body
    - dataclass parameters are parsed from the query string
    - str | None parameters create optional URL segments (two routes registered)
    - int, float, str use Quart's built-in type converters
    - HTTP method is POST if a body param is present, GET otherwise
    """
    original = inspect.unwrap(func)
    path, validated_params, literal_params, body_param, query_param, optional_params = common.build_api_path(original)
    method = "POST" if body_param is not None else "GET"

    async def wrapper(*_args: Any, **kwargs: Any) -> Any:
        await common.run_validators(kwargs, validated_params)
        kwargs.update(literal_params)

        if body_param is not None:
            body_name, body_cls = body_param
            json_data = await quart.request.get_json()
            try:
                kwargs[body_name] = body_cls.model_validate(json_data)
            except pydantic.ValidationError as e:
                raise quart_schema.RequestSchemaValidationError(e) from e

        if query_param is not None:
            query_name, query_cls = query_param
            kwargs[query_name] = _parse_query_args(query_cls, quart.request.args)

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
    for attr in _QUART_ATTRIBUTES:
        if hasattr(func, attr):
            setattr(wrapper, attr, getattr(func, attr))

    # If there are optional params, we need two routes, one with the optional params omitted
    # and one with them all present.
    # AM 26/03/03: This actually only handles the case where there's some required and a single optional, but
    # that's the only case that existed in the original code. Theoretically we could count the optional params and
    # generate the correct number of routes, but that's lot of effort for little gain right now
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

    common.register_route(original, "api", _routes)
    return wrapper


@_BLUEPRINT.before_request
@rate_limiter.rate_limit(500, datetime.timedelta(hours=1))
async def _api_rate_limit() -> None:
    """Set API-wide rate limit"""
    pass


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


def _coerce_query_field(raw: str, field_type: Any, field_name: str) -> Any:
    """Coerce a raw query string value to the expected field type."""
    if field_type is str or field_type == "str":
        return raw
    if field_type is int or field_type == "int":
        try:
            return int(raw)
        except ValueError:
            raise exceptions.BadRequest(f"Query parameter {field_name!r} must be an integer")
    if field_type is bool or field_type == "bool":
        return raw.lower() in ("true", "1", "yes")
    return raw


def _parse_query_args(query_cls: type, args: Any) -> Any:
    """Parse query string parameters into a dataclass instance."""
    field_values: dict[str, Any] = {}
    for field in dataclasses.fields(query_cls):
        raw = args.get(field.name)
        if raw is None:
            if field.default is not dataclasses.MISSING:
                field_values[field.name] = field.default
            elif field.default_factory is not dataclasses.MISSING:
                field_values[field.name] = field.default_factory()
            continue
        field_values[field.name] = _coerce_query_field(raw, field.type, field.name)
    return query_cls(**field_values)


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
