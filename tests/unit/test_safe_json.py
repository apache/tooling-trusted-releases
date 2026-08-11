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
import json
import pathlib

import sqlalchemy.dialects.sqlite as sqlite

import atr.models.safe as safe
import atr.models.sql as sql

# SafeJSON ignores the dialect, but its signature wants one, so hand it the
# SQLite dialect the app actually runs on.
_DIALECT = sqlite.dialect()

# A valid construction example for every concrete safe type. The completeness
# test below fails if a new safe type is added without an entry here, so the
# round-trip coverage can't quietly fall behind the type tree.
_SAFE_TYPE_EXAMPLES: dict[type[safe.SafeType], str] = {
    safe.Alphanumeric: "abc-123",
    safe.OwnerNamespace: "com.example",
    safe.AsfUid: "user1",
    safe.CommitHash: "1a2b3c4d",
    safe.CommitteeKey: "tooling",
    safe.Numeric: "12345",
    safe.ProjectKey: "tooling",
    safe.ReleaseKey: "tooling-0.0.1",
    safe.RelPath: "dir/file.txt",
    safe.RelDirPath: ".",
    safe.RevisionNumber: "00001",
    safe.VersionKey: "0.0.1",
}

# StatePath isn't a SafeType subclass and carries a managed root, so it's built
# and checked on its own rather than from the example table.
_STATE_ROOT = pathlib.Path("/srv/atr/state")
_STATE_PATH = safe.StatePath(_STATE_ROOT / "releases" / "example", _STATE_ROOT)


def test_all_safe_types_round_trip_through_safe_json() -> None:
    # Build a dict holding one instance of every safe type, then put it through
    # the SafeJSON write/read cycle the task_args column uses.
    values: dict[str, object] = {cls.__name__: cls(example) for cls, example in _SAFE_TYPE_EXAMPLES.items()}
    values["StatePath"] = _STATE_PATH

    encoded = sql.SafeJSON().process_bind_param(values, _DIALECT)

    # The point of the test: nothing safe-typed leaks past the write boundary,
    # so the encoded form is plain JSON that the stdlib encoder can handle.
    assert json.loads(json.dumps(encoded)) == encoded

    decoded = sql.SafeJSON().process_result_value(encoded, _DIALECT)
    assert decoded is not None

    # Plain safe types come back as their string form (Pydantic re-types them
    # when a model loads the args); only StatePath rebuilds itself, root and all.
    for cls, example in _SAFE_TYPE_EXAMPLES.items():
        assert decoded[cls.__name__] == str(cls(example))
    assert isinstance(decoded["StatePath"], safe.StatePath)
    assert decoded["StatePath"] == _STATE_PATH
    assert decoded["StatePath"].root == _STATE_PATH.root


def test_every_safe_type_has_a_serialisation_example() -> None:
    missing = _concrete_safe_types() - set(_SAFE_TYPE_EXAMPLES)
    assert not missing, f"New safe type(s) without SafeJSON round-trip coverage: {sorted(c.__name__ for c in missing)}"


def _concrete_safe_types() -> set[type[safe.SafeType]]:
    # Walk the whole SafeType tree. The base SafeType has an empty valid-char
    # set and isn't used directly, so the leaves and intermediate concrete
    # types (Alphanumeric, Numeric) are what we care about.
    found: set[type[safe.SafeType]] = set()
    stack = list(safe.SafeType.__subclasses__())
    while stack:
        cls = stack.pop()
        found.add(cls)
        stack.extend(cls.__subclasses__())
    return found
