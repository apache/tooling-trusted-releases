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

import sqlalchemy
import sqlmodel

import atr.admin as admin


def _table_models() -> set[type]:
    # Every mapped table class, walked from the SQLModel base so we don't have to
    # keep our own list in step - that's the whole point of the guard below.
    discovered: set[type] = set()
    pending = list(sqlmodel.SQLModel.__subclasses__())
    while pending:
        model_class = pending.pop()
        pending.extend(model_class.__subclasses__())
        if getattr(model_class, "__table__", None) is not None:
            discovered.add(model_class)
    return discovered


def test_browsable_models_are_complete() -> None:
    # A new table=True model has to be added to the data browser, or this fails
    missing = _table_models() - set(admin._BROWSABLE_MODELS)
    assert missing == set(), f"tables absent from the data browser: {sorted(c.__name__ for c in missing)}"


def test_sensitive_columns_name_real_columns() -> None:
    # A renamed or dropped secret column would silently stop being redacted, so pin
    # each entry to a browsable model and a column that actually exists on it
    browsable = {model_class.__name__: model_class for model_class in admin._BROWSABLE_MODELS}
    for model_name, columns in admin._SENSITIVE_COLUMNS.items():
        assert model_name in browsable, f"unknown model in sensitive config: {model_name}"
        real_columns = set(sqlalchemy.inspect(browsable[model_name]).columns.keys())
        assert columns <= real_columns, f"{model_name} has no columns {columns - real_columns}"


def test_redact_hides_the_bulk_of_a_secret() -> None:
    secret = "0123456789abcdef0123456789abcdef"
    redacted = admin._redact(secret)
    assert redacted == secret[: admin._REDACTION_VISIBLE_PREFIX] + "..."
    assert secret not in redacted
    assert redacted.isascii()


def test_redact_hides_a_short_value_whole() -> None:
    assert admin._redact("short") == "..."


def test_redact_passes_non_strings_through() -> None:
    assert admin._redact(None) is None
    assert admin._redact(42) == 42
