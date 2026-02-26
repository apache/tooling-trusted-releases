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
from typing import Any, Concatenate, ParamSpec, TypeVar, overload

import asfquart.auth as auth
import asfquart.base as base
import quart

import atr.blueprints.common as common
import atr.log as log
import atr.models.safe as safe
import atr.web as web

_BLUEPRINT_NAME = "get_blueprint"
_BLUEPRINT = quart.Blueprint(_BLUEPRINT_NAME, __name__)
_routes: list[str] = []

_P = ParamSpec("_P")
_R = TypeVar("_R")


def register(app: base.QuartApp) -> tuple[ModuleType, list[str]]:
    import atr.get as get

    app.register_blueprint(_BLUEPRINT)
    return get, _routes


@overload
def typed[**P, R](func: Callable[Concatenate[web.Committer, P], Awaitable[R]]) -> web.RouteFunction[R]: ...


@overload
def typed[**P, R](func: Callable[Concatenate[web.Public, P], Awaitable[R]]) -> web.RouteFunction[R]: ...  # pyright: ignore[reportOverlappingOverload]


def typed(func: Callable[..., Any]) -> web.RouteFunction[Any]:
    """Decorator that derives the URL path from the function's type annotations.

    - Arguments after session are joined with / to make the web path
    - Literal["..."] parameters become literal path segments
    - safe.ProjectName / safe.VersionName parameters are validated via cache/DB
    - int, float use Quart's built-in type converters
    - str parameters pass through as-is
    - check_access is called automatically for committer routes with project_name
    """
    path, validated_params, literal_params, _, public = common.build_path(func)
    project_name_var = next((name for name, type_name in validated_params if type_name is safe.ProjectName), None)
    check_access = not public and (project_name_var is not None)

    async def wrapper(*_args: Any, **kwargs: Any) -> Any:
        enhanced_session = await common.authenticate_public() if public else await common.authenticate()
        await common.run_validators(kwargs, validated_params)
        kwargs.update(literal_params)

        if check_access and (enhanced_session is not None) and (project_name_var is not None):
            await enhanced_session.check_access(str(kwargs[project_name_var]))

        start_time_ns = time.perf_counter_ns()
        response = await func(enhanced_session, **kwargs)
        end_time_ns = time.perf_counter_ns()
        total_ns = end_time_ns - start_time_ns
        total_ms = total_ns // 1_000_000

        log.performance(
            f"GET {path} {func.__name__} = 0 0 {total_ms}",
        )

        return response

    endpoint = func.__module__.replace(".", "_") + "_" + func.__name__
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    wrapper.__annotations__["endpoint"] = _BLUEPRINT_NAME + "." + endpoint

    decorated = wrapper if public else auth.require(auth.Requirements.committer)(wrapper)
    _BLUEPRINT.add_url_rule(path, endpoint=endpoint, view_func=decorated, methods=["GET"])
    common.register_route(func, "get", _routes)

    return decorated
