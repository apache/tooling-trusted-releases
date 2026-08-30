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
import functools
import time
from collections.abc import Awaitable, Callable
from types import ModuleType
from typing import Any, Final

import asfquart.base as base
import pydantic
import quart
import quart.blueprints as blueprints
import quart_rate_limiter as rate_limiter
import quart_schema
import werkzeug.exceptions as exceptions

import atr.blueprints.api_auth as auth
import atr.blueprints.common as common
import atr.errors as errors
import atr.log as log
import atr.storage as storage
import atr.web as web

__all__ = ["auth", "register", "typed"]

_BLUEPRINT_NAME: Final = "api_blueprint"
_BLUEPRINT: Final = quart.Blueprint(_BLUEPRINT_NAME, __name__, url_prefix="/api")
_routes: list[str] = []
_route_auth_schemes: dict[str, str] = {}


def register(app: base.QuartApp) -> tuple[ModuleType, list[str]]:
    import atr.api as api

    app.register_blueprint(_BLUEPRINT)
    return api, _routes


def route_auth_schemes() -> dict[str, str]:
    """Return a snapshot of the (route function name -> auth scheme) map."""
    return dict(_route_auth_schemes)


def typed(
    *,
    auth_scheme: auth.Auth,
    method: str = "",
    response: list[tuple[type[pydantic.BaseModel], int]] | tuple[type[pydantic.BaseModel], int] | None = None,
    rate_limit: tuple[int, datetime.timedelta] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], web.RouteFunction[Any]]:
    """Single decorator for an API route.

    Derives the URL from the handler's typed arguments, then composes the pipeline
    in a fixed order: rate limit, header-credential auth, body parsing,
    body-credential auth, handler. Header auth wraps outside body validation so an
    unauthenticated request is rejected before its body is parsed.
    """

    def decorator(handler: Callable[..., Awaitable[Any]]) -> web.RouteFunction[Any]:
        path, validated_params, literal_params, body_param, _, query_param, optional_params = common.build_api_path(
            handler
        )
        http_method = method or ("POST" if (body_param is not None) else "GET")
        body_safe_params = common.safe_params_for_type(body_param[1]) if (body_param is not None) else []
        query_safe_params = common.safe_params_for_type(query_param[1]) if (query_param is not None) else []

        async def wrapper(*_args: Any, **kwargs: Any) -> Any:
            await common.validate_params(kwargs, validated_params)
            kwargs.update(literal_params)
            await common.confidential_release_block(kwargs, validated_params, None, allow_asf_member=False)

            if body_param is not None:
                await common.parse_body(body_param, body_safe_params, kwargs)
            if query_param is not None:
                await common.parse_query(query_param, query_safe_params, kwargs)

            if auth_scheme in auth.BODY_SCHEMES:
                body = kwargs.get(body_param[0]) if (body_param is not None) else None
                await auth.authenticate_body(auth_scheme, body)

            start_time_ns = time.perf_counter_ns()
            result = await handler(**kwargs)
            total_ms = (time.perf_counter_ns() - start_time_ns) // 1_000_000
            log.performance(f"API {http_method} {path} {handler.__name__} = 0 0 {total_ms}")
            return result

        endpoint = common.setup_wrapper(wrapper, handler, _BLUEPRINT_NAME)
        # Link the wrapper to the original handler so inspect.unwrap (and tests)
        # can recover it; the decorators below chain __wrapped__ too. Empty
        # assigned/updated means we only set __wrapped__, nothing else.
        functools.update_wrapper(wrapper, handler, assigned=(), updated=())

        view = _decorate_view(
            wrapper,
            auth_scheme=auth_scheme,
            response=response,
            query_param=query_param,
            body_param=body_param,
            rate_limit=rate_limit,
        )

        _add_url_rules(view, path, endpoint, http_method, optional_params)
        common.register_route(handler, "api", _routes)
        _route_auth_schemes[handler.__name__] = auth_scheme.value
        return view

    return decorator


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


@_BLUEPRINT.before_request
@rate_limiter.rate_limit(500, datetime.timedelta(hours=1))
async def _api_rate_limit() -> None:
    """Set API-wide rate limit"""
    pass


def _decorate_view(
    view: Callable[..., Any],
    *,
    auth_scheme: auth.Auth,
    response: list[tuple[type[pydantic.BaseModel], int]] | tuple[type[pydantic.BaseModel], int] | None,
    query_param: tuple[str, type] | None,
    body_param: tuple[str, type[pydantic.BaseModel]] | None,
    rate_limit: tuple[int, datetime.timedelta] | None,
) -> Callable[..., Any]:
    # Layer the OpenAPI/validation/auth/rate-limit decorators onto the view in the
    # order they must run: security annotation, response and request validation,
    # then header auth (outside body validation), then rate limiting (outermost).
    view = auth.security_scheme_for(auth_scheme)(view)
    if response is not None:
        for response_model, response_status in response if isinstance(response, list) else [response]:
            view = quart_schema.validate_response(response_model, response_status)(view)
    if query_param is not None:
        view = quart_schema.validate_querystring(query_param[1])(view)
    if body_param is not None:
        view = quart_schema.validate_request(body_param[1])(view)
    if auth_scheme in auth.HEADER_SCHEMES:
        view = _require_header_auth(auth_scheme, view)
    if rate_limit is not None:
        view = rate_limiter.rate_limit(rate_limit[0], rate_limit[1])(view)
    return view


def _exempt_blueprint(app: base.QuartApp) -> None:
    csrf = app.extensions.get("csrf")
    if csrf is not None:
        csrf.exempt(_BLUEPRINT)


@_BLUEPRINT.errorhandler(base.ASFQuartException)
async def _handle_asfquart_exception(err: base.ASFQuartException) -> tuple[quart.Response, int]:
    status = errors.response_status_code(err)
    if status >= 500:
        log.error("Unhandled exception", exc_info=(type(err), err, err.__traceback__))
    return _json_error(errors.message(err), status, error=err)


@_BLUEPRINT.errorhandler(Exception)
async def _handle_generic_exception(err: Exception) -> tuple[quart.Response, int]:
    log.error("Unhandled exception", exc_info=(type(err), err, err.__traceback__))
    return _json_error(errors.message(err), 500, error=err)


@_BLUEPRINT.errorhandler(exceptions.HTTPException)
async def _handle_http_exception(err: exceptions.HTTPException) -> tuple[quart.Response, int]:
    status = errors.response_status_code(err)
    message = err.description or err.name
    if status >= 500:
        log.error("HTTP server exception", exc_info=(type(err), err, err.__traceback__))
    return _json_error(message, status, error=err)


@_BLUEPRINT.errorhandler(exceptions.NotFound)
async def _handle_not_found(err: exceptions.NotFound) -> tuple[quart.Response, int]:
    return _json_error(err.description or err.name, 404)


@_BLUEPRINT.errorhandler(quart_schema.RequestSchemaValidationError)
async def _handle_request_validation(err: quart_schema.RequestSchemaValidationError) -> tuple[quart.Response, int]:
    if not isinstance(err.validation_error, pydantic.ValidationError):
        raise err.validation_error
    verr: pydantic.ValidationError = err.validation_error
    return _json_error("Input validation failed", 400, {"validation_details": verr.errors(include_context=False)})


@_BLUEPRINT.errorhandler(storage.AccessError)
async def _handle_storage_access_error(err: storage.AccessError) -> tuple[quart.Response, int]:
    status = errors.response_status_code(err)
    if status >= 500:
        log.error("Storage access server error", exc_info=(type(err), err, err.__traceback__))
    return _json_error(errors.message(err), status, error=err)


def _json_error(
    message: str,
    status_code: int,
    extra: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> tuple[quart.Response, int]:
    payload = {"error": message}
    payload.update(log.request_context_fields())
    if error is not None:
        # audit_guidance JSON tracebacks expose public ATR code locations but not frame locals
        payload.update(errors.traceback_fields(error, status_code))
    if extra is not None:
        payload.update(extra)
    return quart.jsonify(payload), status_code


def _require_header_auth(scheme: auth.Auth, inner: Callable[..., Any]) -> Callable[..., Any]:
    # Wrap a view so header-credential auth runs before anything it wraps (body
    # validation included). functools.wraps carries the inner OpenAPI attributes
    # and the __wrapped__ chain up to the returned view.
    @functools.wraps(inner)
    async def checked(*args: Any, **kwargs: Any) -> Any:
        await auth.authenticate_header(scheme)
        return await inner(*args, **kwargs)

    return checked


@_BLUEPRINT.record_once
def _setup(state: blueprints.BlueprintSetupState) -> None:
    if isinstance(state.app, base.QuartApp):
        _exempt_blueprint(state.app)
