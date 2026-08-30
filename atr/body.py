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

import functools
import shutil
import tempfile
from collections.abc import Generator
from typing import IO, Any

import quart.formparser as formparser
import quart.wrappers.request as request
import werkzeug.exceptions as exceptions

import atr.config as config
import atr.paths as paths

READ_CHUNK_SIZE = 65536
SPOOL_FREE_MINIMUM = 536870912
SPOOL_MAX_SIZE = 1048576


class Body(request.Body):
    def __init__(self, expected_content_length: int | None, max_content_length: int | None) -> None:
        super().__init__(expected_content_length, max_content_length)
        self._spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_SIZE, dir=spool_dir())
        self._closed = False
        self._read_offset = 0
        self._received = 0
        self._write_offset = 0

    async def __anext__(self) -> bytes:
        if self._must_raise is not None:
            raise self._must_raise
        if not self._complete.is_set():
            await self._has_data.wait()
        if self._must_raise is not None:
            raise self._must_raise
        unread = self._write_offset - self._read_offset
        if self._complete.is_set() and (unread == 0):
            if not self._closed:
                self._spool.truncate(0)
            raise StopAsyncIteration()
        self._spool.seek(self._read_offset)
        data = self._spool.read(min(unread, READ_CHUNK_SIZE))
        self._read_offset += len(data)
        if self._read_offset == self._write_offset:
            self._has_data.clear()
        return data

    def __await__(self) -> Generator[Any, None, Any]:
        if self._must_raise is not None:
            raise self._must_raise
        yield from self._complete.wait().__await__()
        if self._must_raise is not None:
            raise self._must_raise
        self._spool.seek(self._read_offset)
        return self._spool.read()

    def append(self, data: bytes) -> None:
        if (data == b"") or self._closed or (self._must_raise is not None):
            return
        self._received += len(data)
        if (self._max_content_length is not None) and (self._received > self._max_content_length):
            self._must_raise = exceptions.RequestEntityTooLarge()
            self.set_complete()
            return
        new_offset = self._write_offset + len(data)
        try:
            if new_offset > SPOOL_MAX_SIZE:
                disk_growth = new_offset if (self._write_offset <= SPOOL_MAX_SIZE) else len(data)
                if shutil.disk_usage(spool_dir()).free <= (spool_floor() + disk_growth):
                    self._must_raise = exceptions.ServiceUnavailable(
                        "Insufficient free disk space to receive the request body."
                    )
                    self.set_complete()
                    return
            self._spool.seek(self._write_offset)
            self._spool.write(data)
        except OSError as error:
            self._must_raise = error
            self.set_complete()
            return
        self._write_offset = new_offset
        self._has_data.set()

    def clear(self) -> None:
        if self._closed:
            return
        self._read_offset = 0
        self._write_offset = 0
        self._spool.seek(0)
        self._spool.truncate()

    def close(self) -> None:
        self._closed = True
        self._spool.close()


class Request(request.Request):
    body_class = Body
    body: Body

    async def close(self) -> None:
        await super().close()
        self.body.close()

    def make_form_data_parser(self) -> formparser.FormDataParser:
        parser = super().make_form_data_parser()
        parser.stream_factory = part_stream_factory
        return parser


def part_stream_factory(
    total_content_length: int | None,
    content_type: str | None,
    filename: str | None,
    content_length: int | None = None,
) -> IO[bytes]:
    return tempfile.TemporaryFile(dir=spool_dir())


@functools.cache
def spool_dir() -> str:
    return str(paths.get_tmp_dir())


@functools.cache
def spool_floor() -> int:
    return SPOOL_FREE_MINIMUM + config.get().MAX_CONTENT_LENGTH
