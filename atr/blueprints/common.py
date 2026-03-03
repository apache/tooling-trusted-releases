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
from typing import Annotated, Any, Literal, TypeAliasType, get_args, get_origin, get_type_hints

import asfquart.base as base
import asfquart.session
import pydantic

import atr.cache as cache
import atr.db as db
import atr.form as form
import atr.ldap as ldap
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.web as web

QUART_CONVERTERS: dict[Any, str] = {
    int: "int",
    float: "float",
    unsafe.Path: "path",
}

VALIDATED_TYPES: set[Any] = {safe.ProjectName, safe.VersionName}


async def authenticate() -> web.Committer:
    web_session = await asfquart.session.read()
    if web_session is None:
        raise base.ASFQuartException("Not authenticated", errorcode=401)
    if (web_session.uid is None) or (not await ldap.is_active(web_session.uid)):
        asfquart.session.clear()
        raise base.ASFQuartException("Account is disabled", errorcode=401)
    return web.Committer(web_session)


async def authenticate_public() -> web.Public:
    web_session = await asfquart.session.read()
    if web_session is None:
        return None
    else:
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
    - validated_params: (name, type) pairs for URL params to be validated with cache/DB
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
    form_param: tuple[str, type] | None = None

    for ix, param_name in enumerate(params):
        hint = hints.get(param_name)
        if hint is None:
            raise TypeError(f"Parameter {param_name!r} in {func.__name__} has no type annotation")

        if (hint is web.Public) or (hint is web.Committer):
            if ix != 0:
                raise TypeError(f"Parameter {param_name!r} in {func.__name__} must be first")
            public = hint is web.Public
            continue

        if _is_form_type(hint):
            if form_param is not None:
                raise TypeError(f"Parameter {param_name!r} in {func.__name__}: only one Form is allowed")
            form_param = (param_name, hint)
            continue

        segment = _param_to_segment(param_name, hint, func.__name__)
        segments.append(segment)
        if hint in VALIDATED_TYPES:
            validated_params.append((param_name, hint))
        elif get_origin(hint) is Literal:
            literal_params[param_name] = str(get_args(hint)[0])

    path = "/" + "/".join(segments)
    return path, validated_params, literal_params, form_param, public


def build_api_path(
    func: Callable[..., Any],
) -> tuple[
    str,
    list[tuple[str, type]],
    dict[str, str],
    tuple[str, type[pydantic.BaseModel]] | None,
    tuple[str, type] | None,
    list[str],
]:
    """Inspect a function's type hints to build a URL path for an API route.

    Accepts URL path params for data, Literal strings for plain URL text, dataclasses for GET query params
    and Pydantic model params for POST bodies

    Returns (path, validated_params, literal_params, body_param, query_param,
    optional_params) where:
    - validated_params: (name, type) pairs for URL params to be validated with cache/DB
    - literal_params: param name -> literal string value for Literal["..."] params
    - body_param: (name, type) for the single BaseModel param, or None
    - query_param: (name, type) for the single dataclass param, or None
    - optional_params: param names whose type is T | None with a default of None
    - return_type: the return type of the function
    """
    hints = get_type_hints(func, include_extras=True)
    sig = inspect.signature(func)
    params = list(sig.parameters.keys())
    segments: list[str] = []
    validated_params: list[tuple[str, type]] = []
    literal_params: dict[str, str] = {}
    body_param: tuple[str, type[pydantic.BaseModel]] | None = None
    query_param: tuple[str, type] | None = None
    optional_params: list[str] = []

    for param_name in params:
        hint = hints.get(param_name)
        if hint is None:
            raise TypeError(f"Parameter {param_name!r} in {func.__name__} has no type annotation")

        if _is_body_type(hint):
            if body_param is not None:
                raise TypeError(f"Parameter {param_name!r} in {func.__name__}: only one body type is allowed")
            body_param = (param_name, hint)
            continue

        if _is_query_type(hint):
            if query_param is not None:
                raise TypeError(f"Parameter {param_name!r} in {func.__name__}: only one query type is allowed")
            query_param = (param_name, hint)
            continue

        inner, is_optional = _unwrap_optional(hint)
        if is_optional:
            segment = _param_to_segment(param_name, inner, func.__name__)
            segments.append(segment)
            optional_params.append(param_name)
            # Note - this means that safe types which are optional will not get validated - no current use case for this
            continue

        segment = _param_to_segment(param_name, hint, func.__name__)
        segments.append(segment)
        if hint in VALIDATED_TYPES:
            validated_params.append((param_name, hint))
        elif get_origin(hint) is Literal:
            literal_params[param_name] = str(get_args(hint)[0])

    path = "/" + "/".join(segments)
    return path, validated_params, literal_params, body_param, query_param, optional_params


def register_route(func: Callable[..., Any], prefix: str, routes: list[str]) -> None:
    module_name = func.__module__.split(".")[-1]
    routes.append(f"{prefix}.{module_name}.{func.__name__}")


async def run_validators(kwargs: dict[str, Any], validated_params: list[tuple[str, type]]) -> None:
    """Validate URL parameters in order, using the cache/DB validators."""
    project_name_param = "project_name"
    for param_name, param_type in validated_params:
        raw = kwargs[param_name]
        if param_type is safe.ProjectName:
            kwargs[param_name] = await validate_project(raw)
            project_name_param = param_name
        elif param_type is safe.VersionName:
            project_name = kwargs.get(project_name_param, "")
            kwargs[param_name] = await validate_version(project_name, raw)


async def validate_project(raw: str) -> safe.ProjectName:
    if cache.project_version_has_project(raw):
        return safe.ProjectName(raw)
    async with db.session() as data:
        project = await data.project(name=raw, status=sql.ProjectStatus.ACTIVE, _committee=False).get()
    if project is None:
        raise base.ASFQuartException(f"Project {raw!r} not found", errorcode=404)
    return safe.ProjectName(project.name)


async def validate_version(project_name: safe.ProjectName, raw: str) -> safe.VersionName:
    if cache.project_version_has_version(project_name, raw):
        return safe.VersionName(raw)
    async with db.session() as data:
        release = await data.release(
            project_name=str(project_name),
            version=raw,
            _project=False,
            _committee=False,
        ).get()
    if release is None:
        raise base.ASFQuartException(f"Version {raw!r} not found for project {project_name!s}", errorcode=404)
    return safe.VersionName(release.version)


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
    if origin is not types.UnionType and origin is not typing.Union:
        return hint, False
    args = get_args(hint)
    non_none = [a for a in args if a is not type(None)]
    if len(non_none) == 1 and type(None) in args:
        return non_none[0], True
    return hint, False
