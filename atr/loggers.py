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

from __future__ import annotations

import datetime
import json
import logging
import logging.handlers
import queue
import sys
from typing import TYPE_CHECKING, Any

import structlog

import atr.daylog as daylog

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Sequence


class AuditHandler(logging.Handler):
    def __init__(self, directory: pathlib.Path) -> None:
        super().__init__()
        self.directory = directory

    def emit(self, record: logging.LogRecord) -> None:
        try:
            daylog.append(self.directory, record.name, record.levelname.lower(), _json_event(record.getMessage()))
        except Exception:
            self.handleError(record)


def configure_structlog(shared_processors: Sequence[structlog.types.Processor]) -> None:
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def create_json_formatter(
    shared_processors: Sequence[structlog.types.Processor],
) -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _parse_json_event,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=list(shared_processors),
    )


def create_output_formatter(
    shared_processors: Sequence[structlog.types.Processor],
    renderer: structlog.types.Processor,
) -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=list(shared_processors),
    )


def setup_audit_logger(directory: pathlib.Path) -> logging.handlers.QueueListener:
    daylog.initialise(directory)
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    listener = logging.handlers.QueueListener(log_queue, AuditHandler(directory))
    listener.start()
    queue_handler = logging.handlers.QueueHandler(log_queue)
    for name in ("atr.auth", "atr.keys.submitted", "atr.storage.audit"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(queue_handler)
        logger.propagate = False
    return listener


def setup_dedicated_file_logger(
    logger_name: str,
    file_path: str,
    processors: Sequence[structlog.types.Processor],
    queue_handler_class: type[logging.handlers.QueueHandler] = logging.handlers.QueueHandler,
) -> logging.handlers.QueueListener:
    handler = logging.FileHandler(file_path, encoding="utf-8")
    handler.setFormatter(create_json_formatter(processors))

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    listener = logging.handlers.QueueListener(log_queue, handler)
    listener.start()

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(queue_handler_class(log_queue))
    logger.propagate = False

    return listener


def shared_processors() -> list[structlog.types.Processor]:
    return [
        _resolve_exc_info,
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        _timestamp,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]


def _json_event(event: Any) -> Any:
    if isinstance(event, str) and event.startswith("{"):
        try:
            return json.loads(event)
        except json.JSONDecodeError:
            pass
    return event


def _parse_json_event(
    _logger: structlog.types.WrappedLogger,
    _method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    if event_dict.get("logger") != "atr.tasks.log":
        return event_dict
    event_dict["event"] = _json_event(event_dict.get("event"))
    return event_dict


def _resolve_exc_info(
    _logger: structlog.types.WrappedLogger,
    _method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    # exc_info=True means "the exception being handled", which only sys.exc_info on the logging
    # thread can see. Our records are formatted later on a queue listener thread, where it would
    # find nothing, so we capture the exception here while it's still in hand
    if event_dict.get("exc_info") is True:
        event_dict["exc_info"] = sys.exc_info()
    return event_dict


def _timestamp(
    _logger: structlog.types.WrappedLogger,
    _method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="microseconds")
    event_dict["timestamp"] = now.replace("+00:00", "Z")
    return event_dict
