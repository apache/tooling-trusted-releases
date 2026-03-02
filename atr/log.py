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

import collections
import inspect
import logging
import logging.handlers
import queue
import threading
from typing import Any, Final

import structlog

PERFORMANCE: logging.Logger | None = None

_global_recent_logs: collections.deque[str] | None = None
_global_recent_logs_lock = threading.Lock()


class BufferingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if _global_recent_logs is None:
            return
        try:
            msg = self.format(record)
            with _global_recent_logs_lock:
                _global_recent_logs.append(msg)
        except Exception:
            self.handleError(record)


class StructlogQueueHandler(logging.handlers.QueueHandler):
    """QueueHandler that preserves structlog's record.msg dict."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # Don't call format() - it would convert the dict msg to a string
        return record


def add_context(**kwargs):
    """Add context to the request log"""
    structlog.contextvars.bind_contextvars(**kwargs)


def caller_name(depth: int = 1) -> str:
    frame = inspect.currentframe()
    for _ in range(depth + 1):
        if frame is None:
            break
        frame = frame.f_back

    if frame is None:
        return __name__

    module = frame.f_globals.get("__name__", python_repr("unknown"))
    func = frame.f_code.co_name

    if func == python_repr("module"):
        # We're at the top level
        return module

    # Are we in a class?
    # There is probably a better way to do this
    cls_name = None
    if "self" in frame.f_locals:
        cls_name = frame.f_locals["self"].__class__.__name__
    elif ("cls" in frame.f_locals) and isinstance(frame.f_locals["cls"], type):
        cls_name = frame.f_locals["cls"].__name__

    if cls_name:
        name = f"{module}.{cls_name}.{func}"
    else:
        name = f"{module}.{func}"

    return name


def clear_context():
    """Clear context from the request log"""
    structlog.contextvars.clear_contextvars()


def create_debug_handler() -> logging.Handler:
    global _global_recent_logs
    _global_recent_logs = collections.deque(maxlen=100)
    handler = BufferingHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s"))
    handler.setLevel(logging.DEBUG)
    return handler


def critical(msg: str, **kwargs) -> None:
    _event(logging.CRITICAL, msg, **kwargs)


def debug(msg: str, **kwargs) -> None:
    _event(logging.DEBUG, msg, **kwargs)


def error(msg: str, **kwargs) -> None:
    _event(logging.ERROR, msg, **kwargs)


def exception(msg: str, **kwargs) -> None:
    _event(logging.ERROR, msg, exc_info=True, **kwargs)


def failed_authentication(reason: str) -> None:
    warning(
        "Authentication failed",
        reason=reason,
    )


def get_context(arg) -> Any | None:
    """Get context from the request log"""
    return structlog.contextvars.get_contextvars().get(arg, None)


def get_recent_logs() -> list[str] | None:
    if _global_recent_logs is None:
        return None
    with _global_recent_logs_lock:
        return list(_global_recent_logs)


def info(msg: str, **kwargs) -> None:
    _event(logging.INFO, msg, **kwargs)


def interface_name(depth: int = 1) -> str:
    return caller_name(depth=depth)


def log(level: int, msg: str, **kwargs) -> None:
    # Custom log level
    _event(level, msg, **kwargs)


def performance(msg: str) -> None:
    if PERFORMANCE is not None:
        PERFORMANCE.info(msg)


def performance_init() -> None:
    global PERFORMANCE
    PERFORMANCE = _performance_logger()


def python_repr(object_name: str) -> str:
    return f"<{object_name}>"


def set_asf_uid(asfuid: str | None) -> None:
    add_context(asfuid=asfuid)


def warning(msg: str, **kwargs) -> None:
    _event(logging.WARNING, msg, **kwargs)


def _caller_logger(depth: int = 1) -> logging.Logger:
    return structlog.getLogger(caller_name(depth))


def _event(level: int, msg: str, stacklevel: int = 3, exc_info: bool = False, **kwargs) -> None:
    logger = _caller_logger(depth=3)
    # Stack level 1 is *here*, 2 is the caller, 3 is the caller of the caller
    # I.e. _event (1), log.* (2), actual caller (3)
    # TODO: We plan to use t-strings instead of the present f-strings for all logging calls
    # To do so, however, we first need to migrate to Python 3.14
    # https://github.com/apache/tooling-trusted-releases/issues/339
    # https://github.com/apache/tooling-trusted-releases/issues/346
    # The stacklevel and exc_info keyword arguments are not available as parameters
    # Therefore this should be safe even with an untrusted msg template
    logger.log(level, msg, stacklevel=stacklevel, exc_info=exc_info, **kwargs)


def _performance_logger() -> logging.Logger:
    import atr.config as config

    class MicrosecondsFormatter(logging.Formatter):
        # Answers on a postcard if you know why Python decided to use a comma by default
        default_msec_format = "%s.%03d"

    performance: Final = logging.getLogger("log.performance")
    # Use custom formatter that properly includes microseconds
    # TODO: Is this actually UTC?
    performance_handler: Final = logging.FileHandler(config.get().PERFORMANCE_LOG_FILE, encoding="utf-8")
    performance_handler.setFormatter(MicrosecondsFormatter("%(asctime)s - %(message)s"))
    performance_queue = queue.Queue(-1)
    performance_listener = logging.handlers.QueueListener(performance_queue, performance_handler)
    performance_listener.start()
    performance.addHandler(StructlogQueueHandler(performance_queue))
    performance.setLevel(logging.INFO)
    # If we don't set propagate to False then it logs to the term as well
    performance.propagate = False

    return performance
