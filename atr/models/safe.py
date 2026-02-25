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


class ProjectName:
    """A project name that has been validated against the cache or database."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ProjectName):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        return f"ProjectName({self._value!r})"

    def __str__(self) -> str:
        return self._value


class VersionName:
    """A version name that has been validated against the cache or database."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, VersionName):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        return f"VersionName({self._value!r})"

    def __str__(self) -> str:
        return self._value
