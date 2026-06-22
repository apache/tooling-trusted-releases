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

import asfquart.auth as auth
import asfquart.base as base
import quart

import atr.blueprints.common as common
import atr.log as log
import atr.web as web

_BLUEPRINT_NAME: Final = "get_blueprint"
_BLUEPRINT: Final = quart.Blueprint(_BLUEPRINT_NAME, __name__)
_routes: list[str] = []


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

    - Literal["..."] parameters become literal path segments
    - safe.SafeType subclass parameters are validated from the URL path
    - int, float use Quart's built-in type converters
    """
    path, validated_params, literal_params, _, public = common.build_path(func)

    async def wrapper(*_args: Any, **kwargs: Any) -> Any:
        enhanced_session = await common.authenticate_public() if public else await common.authenticate()
        await common.validate_params(kwargs, validated_params)
        kwargs.update(literal_params)
        await common.confidential_release_block(kwargs, validated_params, enhanced_session, allow_asf_member=True)

        start_time_ns = time.perf_counter_ns()
        response = await func(enhanced_session, **kwargs)
        end_time_ns = time.perf_counter_ns()
        total_ns = end_time_ns - start_time_ns
        total_ms = total_ns // 1_000_000

        log.performance(
            f"GET {path} {func.__name__} = 0 0 {total_ms}",
        )

        return response

    endpoint = common.setup_wrapper(wrapper, func, _BLUEPRINT_NAME)

    decorated = wrapper if public else auth.require(auth.Requirements.committer)(wrapper)
    _BLUEPRINT.add_url_rule(path, endpoint=endpoint, view_func=decorated, methods=["GET"])
    common.register_route(func, "get", _routes)

    return decorated
