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
import pydantic
import quart

import atr.blueprints.common as common
import atr.form
import atr.log as log
import atr.web as web

_BLUEPRINT_NAME: Final = "post_blueprint"
_BLUEPRINT: Final = quart.Blueprint(_BLUEPRINT_NAME, __name__)
_routes: list[str] = []


def register(app: base.QuartApp) -> tuple[ModuleType, list[str]]:
    import atr.post as post

    app.register_blueprint(_BLUEPRINT)
    return post, _routes


@overload
def typed[**P, R](func: Callable[Concatenate[web.Committer, P], Awaitable[R]]) -> web.RouteFunction[R]: ...


@overload
def typed[**P, R](func: Callable[Concatenate[web.Public, P], Awaitable[R]]) -> web.RouteFunction[R]: ...  # pyright: ignore[reportOverlappingOverload]


def typed(func: Callable[..., Any]) -> web.RouteFunction[Any]:
    """Decorator that derives the URL path from the function's type annotations.

    - Literal["..."] parameters become literal path segments
    - safe.SafeType subclass parameters are validated from the URL path
    - int, float use Quart's built-in type converters
    - A single form.Form subclass parameter is validated from the request body and injected
    """
    path, validated_params, literal_params, form_param, _, public = common.build_path(func)
    form_safe_params = common.safe_params_for_type(form_param[1]) if (form_param is not None) else []

    async def wrapper(*_args: Any, **kwargs: Any) -> Any:
        enhanced_session = await common.authenticate_public() if public else await common.authenticate()
        await common.validate_params(kwargs, validated_params)
        kwargs.update(literal_params)
        await common.confidential_release_block(kwargs, validated_params, enhanced_session, allow_asf_member=True)

        if form_param is not None:
            form_param_name, form_cls = form_param
            context: dict[str, Any] = {"kwargs": kwargs, "session": enhanced_session}
            try:
                match enhanced_session:
                    case web.Committer() as committer:
                        kwargs[form_param_name] = await committer.form_validate(form_cls, context)
                    case None:
                        form_data = await atr.form.quart_request()
                        kwargs[form_param_name] = atr.form.validate(form_cls, form_data, context=context)
            except pydantic.ValidationError as e:
                return await common.flash_form_error(form_cls, e)
            if form_safe_params:
                await common.validate_safe_fields(kwargs[form_param_name], form_safe_params, kwargs)

        start_time_ns = time.perf_counter_ns()
        response = await func(enhanced_session, **kwargs)
        end_time_ns = time.perf_counter_ns()
        total_ns = end_time_ns - start_time_ns
        total_ms = total_ns // 1_000_000

        log.performance(f"POST {path} {func.__name__} = 0 0 {total_ms}")

        return response

    endpoint = common.setup_wrapper(wrapper, func, _BLUEPRINT_NAME)

    decorated = wrapper if public else auth.require(auth.Requirements.committer)(wrapper)
    _BLUEPRINT.add_url_rule(path, endpoint=endpoint, view_func=decorated, methods=["POST"])
    common.register_route(func, "post", _routes)

    return decorated
