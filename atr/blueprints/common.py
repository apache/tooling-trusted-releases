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
import inspect
import types
import typing
from collections.abc import Callable
from typing import Annotated, Any, Final, Literal, TypeAliasType, get_args, get_origin, get_type_hints

import asfquart.base as base
import pydantic
import quart
import quart_schema
import werkzeug.exceptions as exceptions

import atr.form as form
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.sessions as sessions
import atr.web as web

QUART_CONVERTERS: Final[dict[Any, str]] = {
    int: "int",
    float: "float",
    safe.RelPath: "path",
    unsafe.Path: "path",
}

VALIDATED_TYPES: Final[set[Any]] = {
    safe.Alphanumeric,
    safe.CommitteeKey,
    safe.ProjectKey,
    safe.RelPath,
    safe.RevisionNumber,
    safe.VersionKey,
    unsafe.UnsafeStr,
}


async def authenticate() -> web.Committer:
    web_session = await sessions.read()
    if not isinstance(web_session, sql.UserSession):
        raise base.ASFQuartException("Not authenticated", errorcode=401)

    # if not await ldap.is_active(web_session.uid):
    #     await sessions.deleted_or_banned(web_session.uid)
    #     raise base.ASFQuartException("Account is disabled", errorcode=401)

    # admin_uid = web_session.admin_uid
    # if isinstance(admin_uid, str) and admin_uid and (not await ldap.is_active(admin_uid)):
    #     await sessions.deleted_or_banned(admin_uid)
    #     raise base.ASFQuartException("Account is disabled", errorcode=401)

    return web.Committer(web_session)


async def authenticate_public() -> web.Public:
    try:
        return await authenticate()
    except base.ASFQuartException:
        return None


def build_path(
    func: Callable[..., Any],
) -> tuple[str, list[tuple[str, type]], dict[str, str], tuple[str, type] | None, bool]:
    """Inspect a function's type hints to build a URL path and a validation plan.

    Accepts URL path params for data, Literal strings for plain URL text, and Form params for POST bodies
    Validates that the session param (web.Committer or web.Public) is first, and that only one Form param is allowed

    Returns (path, validated_params, literal_params, form_param, public) where:
    - validated_params: (name, type) pairs for safe.SafeType subclass URL params to be validated
    - literal_params: param name → literal string value for Literal["..."] params
    - form_param: (name, type) for the single form.Form subclass param, or None
    - public: True if the session type is web.Public
    """
    hints = get_type_hints(func, include_extras=True)
    params = list(inspect.signature(func).parameters.keys())
    public = False
    segments: list[str] = []
    validated_params: list[tuple[str, type]] = []
    literal_params: dict[str, str] = {}
    unique = _UniqueParams()

    for ix, param_name in enumerate(params):
        hint = hints.get(param_name)
        if hint is None:
            raise TypeError(f"Parameter {param_name!r} in {func.__name__} has no type annotation")

        if (hint is web.Public) or (hint is web.Committer):
            if ix != 0:
                raise TypeError(f"Parameter {param_name!r} in {func.__name__} must be first")
            public = hint is web.Public
            continue

        if unique.check(hint, param_name, func.__name__):
            continue

        _classify_url_param(param_name, hint, func.__name__, segments, validated_params, literal_params)

    path = "/" + "/".join(segments)
    return path, validated_params, literal_params, unique.form, public


def build_api_path(
    func: Callable[..., Any],
) -> tuple[
    str,
    list[tuple[str, type]],
    dict[str, str],
    tuple[str, type[pydantic.BaseModel]] | None,
    tuple[str, type] | None,
    tuple[str, type] | None,
    list[str],
]:
    """Inspect a function's type hints to build a URL path for an API route.

    Accepts URL path params for data, Literal strings for plain URL text, dataclasses for GET query params
    and Pydantic model params for POST bodies

    Returns (path, validated_params, literal_params, body_param, form_param, query_param,
    optional_params) where:
    - validated_params: (name, type) pairs for safe.SafeType subclass URL params to be validated
    - literal_params: param name -> literal string value for Literal["..."] params
    - body_param: (name, type) for the single BaseModel param, or None
    - form_param: (name, type) for the single form.Form subclass param, or None
    - query_param: (name, type) for the single dataclass param, or None
    - optional_params: param names whose type is T | None with a default of None
    """
    hints = get_type_hints(func, include_extras=True)
    sig = inspect.signature(func)
    params = list(sig.parameters.keys())
    segments: list[str] = []
    validated_params: list[tuple[str, type]] = []
    literal_params: dict[str, str] = {}
    unique = _UniqueParams()
    optional_params: list[str] = []

    for ix, param_name in enumerate(params):
        hint = hints.get(param_name)
        if hint is None:
            raise TypeError(f"Parameter {param_name!r} in {func.__name__} has no type annotation")

        if (hint is web.Public) or (hint is web.Committer):
            if ix != 0:
                raise TypeError(f"Parameter {param_name!r} in {func.__name__} must be first")
            continue

        if unique.check(hint, param_name, func.__name__):
            continue

        inner, is_optional = _unwrap_optional(hint)
        if is_optional:
            segments.append(_param_to_segment(param_name, inner, func.__name__))
            optional_params.append(param_name)
            if inner in VALIDATED_TYPES:
                validated_params.append((param_name, inner))
            continue

        _classify_url_param(param_name, hint, func.__name__, segments, validated_params, literal_params)

    path = "/" + "/".join(segments)
    return path, validated_params, literal_params, unique.body, unique.form, unique.query, optional_params


def setup_wrapper(wrapper: Callable[..., Any], func: Callable[..., Any], blueprint_name: str) -> str:
    """Set standard metadata on a route wrapper and return the endpoint name."""
    endpoint = func.__module__.replace(".", "_") + "_" + func.__name__
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    wrapper.__annotations__["endpoint"] = blueprint_name + "." + endpoint
    return endpoint


def register_route(func: Callable[..., Any], prefix: str, routes: list[str]) -> None:
    module_name = func.__module__.split(".")[-1]
    routes.append(f"{prefix}.{module_name}.{func.__name__}")


def safe_params_for_type(cls: type) -> list[tuple[str, type]]:
    """Return (field_name, safe_type) pairs for fields typed as a validated safe type."""
    try:
        hints = get_type_hints(cls)
    except Exception:
        return []
    return [(name, hint) for name, hint in hints.items() if hint in VALIDATED_TYPES]


async def validate_params(kwargs: dict[str, Any], known_params: list[tuple[str, type]]) -> None:
    """Validate URL parameters in order, using the type-specific validators."""
    for param_name, param_type in known_params:
        raw = kwargs.get(param_name)
        if raw is None:
            continue
        if param_type is unsafe.UnsafeStr:
            kwargs[param_name] = unsafe.UnsafeStr(raw)
        elif issubclass(param_type, safe.SafeType):
            try:
                kwargs[param_name] = param_type(raw)
            except ValueError:
                raise base.ASFQuartException(f"Parameter {param_name!r} is invalid. ")


async def validate_safe_fields(
    instance: Any,
    safe_params: list[tuple[str, type]],
    context: dict[str, Any],
) -> None:
    """Validate safe-typed fields on a body, query, or form instance via cache/DB lookup.

    Context should contain any URL params already validated, so that validate_version
    can find a project_key that lives in the URL rather than the instance.
    """
    temp = dict(context)
    for name, _ in safe_params:
        value = getattr(instance, name, None)
        if value is not None:
            temp[name] = str(value)
    await validate_params(temp, [(n, t) for n, t in safe_params if n in temp])
    for name, _ in safe_params:
        if name in temp:
            setattr(instance, name, temp[name])


async def flash_form_error(form_cls: type, error: pydantic.ValidationError) -> Any:
    """Flash form validation errors and return a redirect to the current page."""
    import json

    errors = error.errors()
    if len(errors) == 0:
        raise RuntimeError("Validation failed, but no errors were reported")
    form_data_raw = await form.quart_request()
    flash_data = form.flash_error_data(form_cls, errors, form_data_raw)
    summary = form.flash_error_summary(errors, flash_data)
    await quart.flash(summary, category="error")
    await quart.flash(json.dumps(flash_data), category="form-error-data")
    return quart.redirect(quart.request.path)


async def parse_body(
    body_param: tuple[str, type[pydantic.BaseModel]],
    safe_params: list[tuple[str, type]],
    kwargs: dict[str, Any],
) -> None:
    """Parse and validate a JSON body parameter, adding it to kwargs."""
    body_name, body_cls = body_param
    json_data = await quart.request.get_json()
    try:
        body_instance = body_cls.model_validate(json_data)
    except pydantic.ValidationError as e:
        raise quart_schema.RequestSchemaValidationError(e) from e
    if safe_params:
        await validate_safe_fields(body_instance, safe_params, kwargs)
    kwargs[body_name] = body_instance


async def parse_query(
    query_param: tuple[str, type],
    safe_params: list[tuple[str, type]],
    kwargs: dict[str, Any],
) -> None:
    """Parse and validate query string parameters, adding them to kwargs."""
    query_name, query_cls = query_param
    query_instance = _parse_query_args(query_cls, quart.request.args)
    if safe_params:
        await validate_safe_fields(query_instance, safe_params, kwargs)
    kwargs[query_name] = query_instance


def _coerce_query_field(raw: str, field_type: Any, field_name: str) -> Any:
    """Coerce a raw query string value to the expected field type."""
    if (field_type is str) or (field_type == "str"):
        return raw
    if (field_type is int) or (field_type == "int"):
        try:
            return int(raw)
        except ValueError:
            raise exceptions.BadRequest(f"Query parameter {field_name!r} must be an integer")
    if (field_type is bool) or (field_type == "bool"):
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


@dataclasses.dataclass
class _UniqueParams:
    """Tracks the at-most-one body, form, and query parameters during path building."""

    body: tuple[str, type[pydantic.BaseModel]] | None = None
    form: tuple[str, type] | None = None
    query: tuple[str, type] | None = None

    def check(self, hint: Any, param_name: str, func_name: str) -> bool:
        """If hint is a body/form/query type, store it (ensuring uniqueness). Return True if matched."""
        if _is_body_type(hint):
            if self.body is not None:
                raise TypeError(f"Parameter {param_name!r} in {func_name}: only one body type is allowed")
            self.body = (param_name, hint)
            return True

        if _is_form_type(hint):
            if self.form is not None:
                raise TypeError(f"Parameter {param_name!r} in {func_name}: only one Form is allowed")
            self.form = (param_name, hint)
            return True

        if _is_query_type(hint):
            if self.query is not None:
                raise TypeError(f"Parameter {param_name!r} in {func_name}: only one query type is allowed")
            self.query = (param_name, hint)
            return True

        return False


def _classify_url_param(
    param_name: str,
    hint: Any,
    func_name: str,
    segments: list[str],
    validated_params: list[tuple[str, type]],
    literal_params: dict[str, str],
) -> None:
    """Build a URL segment for a parameter and classify it as validated or literal."""
    segment = _param_to_segment(param_name, hint, func_name)
    segments.append(segment)
    if hint in VALIDATED_TYPES:
        validated_params.append((param_name, hint))
    elif get_origin(hint) is Literal:
        literal_params[param_name] = str(get_args(hint)[0])
    elif hint is str:
        raise TypeError(f"Parameter {param_name!r} in {func_name} is unguarded str")


def _is_body_type(hint: Any) -> bool:
    """Check if a type hint is a pydantic BaseModel subclass (but not a Form)."""
    if not isinstance(hint, type):
        return False
    if issubclass(hint, form.Form):
        return False
    return issubclass(hint, pydantic.BaseModel)


def _is_form_type(hint: Any) -> bool:
    """Check if a type hint represents a form.Form subclass or Annotated discriminated union of forms."""
    if isinstance(hint, type) and issubclass(hint, form.Form):
        return True
    # Unwrap TypeAliasType to get the underlying type
    if isinstance(hint, TypeAliasType):
        hint = hint.__value__
    if get_origin(hint) is Annotated:
        args = get_args(hint)
        return (len(args) >= 2) and (form.DISCRIMINATOR in args[1:])
    return False


def _is_query_type(hint: Any) -> bool:
    """Check if a type hint is a dataclass (used for query-string params)."""
    return dataclasses.is_dataclass(hint) and isinstance(hint, type)


def _param_to_segment(param_name: str, hint: Any, func_name: str) -> str:
    """Convert a single parameter's type hint into a URL path segment."""
    if get_origin(hint) is Literal:
        return str(get_args(hint)[0])
    if hint in VALIDATED_TYPES:
        return f"<{param_name}>"
    if hint in QUART_CONVERTERS:
        return f"<{QUART_CONVERTERS[hint]}:{param_name}>"
    if hint is str:
        return f"<{param_name}>"
    raise TypeError(f"Parameter {param_name!r} in {func_name} has unsupported type {hint!r}")


def _unwrap_optional(hint: Any) -> tuple[Any, bool]:
    """If hint is T | None, return (T, True). Otherwise return (hint, False).

    Handles both ``str | None`` (types.UnionType) and ``typing.Optional[str]``
    (typing.Union).
    """
    origin = get_origin(hint)
    if (origin is not types.UnionType) and (origin is not typing.Union):
        return hint, False
    args = get_args(hint)
    non_none = [a for a in args if a is not type(None)]
    if (len(non_none) == 1) and (type(None) in args):
        return non_none[0], True
    return hint, False


QUART_ATTRIBUTES = [
    quart_schema.validation.QUART_SCHEMA_HEADERS_ATTRIBUTE,
    quart_schema.validation.QUART_SCHEMA_RESPONSE_ATTRIBUTE,
    quart_schema.openapi.QUART_SCHEMA_SECURITY_ATTRIBUTE,
    quart_schema.openapi.QUART_SCHEMA_TAG_ATTRIBUTE,
    quart_schema.openapi.QUART_SCHEMA_HIDDEN_ATTRIBUTE,
    quart_schema.openapi.QUART_SCHEMA_DEPRECATED_ATTRIBUTE,
    quart_schema.openapi.QUART_SCHEMA_OPERATION_ID_ATTRIBUTE,
]
