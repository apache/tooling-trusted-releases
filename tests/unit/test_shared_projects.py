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

import pytest

import atr.shared.projects as shared_projects


@pytest.mark.parametrize(
    "name",
    [
        # Baselines — full names.
        "Apache Example",
        "Apache Example Components",
        "Apache Commons Codec",
        "Apache Maven",
        "Apache HTTP",
        # Suffix-only — the form's UI submits this shape after the prefix
        # addon makes "Apache" non-editable.
        "Example",
        "Example Components",
        "Commons Codec",
        "Maven",
        "HTTP",
        # Whitespace normalisation.
        "  Apache   Example  ",
        "  Example   Components  ",
        # Allowed irregular words.
        "Apache Lucene.NET",
        "Apache C++",
        "Apache jclouds",
        "Apache .NET",
        ".NET",
        "Apache CouchDB for Erlang",
        "CouchDB for Erlang",
        # Regression cases from #1256: real Apache projects with `-` or `_`.
        "Apache Empire-db",
        "Empire-db",
        "Apache mod_jk",
        "mod_jk",
        "Apache mod_perl",
        "Apache mod_python",
        "Apache mod_ftp",
        "Apache mod_jk Connector",
        # Whimsy spelling variants and parenthesized abbreviations.
        "Apache Lucene.Net",
        "Lucene.Net",
        "Lucene.net",
        "Apache Portable Runtime (APR)",
        "Portable Runtime (APR)",
    ],
)
def test_validate_display_name_accepts(name: str) -> None:
    # Should not raise.
    shared_projects._validate_display_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "",  # empty
        "   ",  # whitespace only
        "lowercase",  # word not in any case class
        "Apache lowercase",  # same, with explicit prefix
        "123Numbers",  # starts with a digit
        "With|Pipe",  # disallowed character
        "Has Slash/Char",  # disallowed character
        "Has<Tag>",  # disallowed character
    ],
)
def test_validate_display_name_rejects(name: str) -> None:
    with pytest.raises(ValueError):
        shared_projects._validate_display_name(name)


def test_validate_display_name_returns_normalised_value() -> None:
    assert shared_projects._validate_display_name("  Apache   Example  ") == "Apache Example"


def test_validate_display_name_prepends_apache_when_omitted() -> None:
    assert shared_projects._validate_display_name("Example") == "Apache Example"
    assert shared_projects._validate_display_name("Example Components") == "Apache Example Components"


def test_validate_display_name_accepts_explicit_prefix() -> None:
    # Users who type "Apache" by habit shouldn't be punished.
    assert shared_projects._validate_display_name("Apache Example") == "Apache Example"


@pytest.mark.parametrize(
    "raw",
    [
        "apache Example",
        "APACHE Example",
        "ApAcHe Example",
        "apache   Example",  # extra whitespace between Apache and the rest
        "  apache Example  ",
    ],
)
def test_validate_display_name_strips_apache_case_insensitively(raw: str) -> None:
    assert shared_projects._validate_display_name(raw) == "Apache Example"


@pytest.mark.parametrize(
    "raw",
    [
        "Apache",
        "apache",
        "APACHE",
        "Apache ",  # trailing space, nothing after
    ],
)
def test_validate_display_name_rejects_bare_apache(raw: str) -> None:
    with pytest.raises(ValueError):
        shared_projects._validate_display_name(raw)
