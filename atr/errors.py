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

import traceback
from typing import Any

import asfquart.base as base
import quart
import werkzeug.exceptions as exceptions


def action_error_response(
    error: BaseException,
    *,
    summary: str | None = None,
    status: int | None = None,
) -> tuple[quart.Response, int]:
    if status is None:
        status = response_status_code(error)
    text = summary if (summary is not None) else message(error)
    payload: dict[str, Any] = {"ok": False, "message": text}
    payload.update(traceback_fields(error, status))
    return quart.jsonify(payload), status


def message(error: BaseException) -> str:
    text = str(error)
    if text == "":
        return type(error).__name__
    return text


def response_status_code(error: BaseException, default: int = 500) -> int:
    if isinstance(error, exceptions.HTTPException):
        return error.code or default
    if isinstance(error, base.ASFQuartException):
        errorcode = getattr(error, "errorcode", None)
        if isinstance(errorcode, int):
            return errorcode or default
    status = getattr(error, "status", None)
    if isinstance(status, int):
        return status or default
    return default


# audit_guidance ATR source code is public so stack locations are not secret application information
def should_show_traceback(status_code: int) -> bool:
    return status_code == 500


def traceback_fields(error: BaseException, status_code: int) -> dict[str, Any]:
    if not should_show_traceback(status_code):
        return {}
    return {
        "exception_type": type(error).__name__,
        "traceback": traceback_text(error),
    }


def traceback_text(error: BaseException) -> str:
    # audit_guidance Frame locals are excluded so request data and token values are not copied into tracebacks
    tb = traceback.TracebackException.from_exception(error, capture_locals=False)
    return "".join(tb.format())
