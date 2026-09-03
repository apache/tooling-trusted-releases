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

import datetime
from types import SimpleNamespace

import pytest

import atr.cle as cle
import atr.models.cle as cle_lib
import atr.models.sql as sql


def test_identifier_renders_apache_purl():
    project = SimpleNamespace(key="myproject", committee_key="mycommittee")
    assert cle._identifier(project) == "pkg:sid/apache.org/the+asf/myproject"


def test_project_document_emits_default_support_definition_when_eod_or_eos_present():
    project = SimpleNamespace(key="example", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    eos = _event(
        event=sql.LifecycleEventType.EOS,
        effective=datetime.datetime(2026, 12, 1, tzinfo=datetime.UTC),
        cycle_key="example-default",
        row_id=1,
    )
    doc = cle.project_document(project, [eos], [], now=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC))
    assert doc["definitions"] == {"support": [{"id": "default", "description": "Apache project community support"}]}


def test_project_document_emits_no_events_when_input_empty():
    project = SimpleNamespace(key="example", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    doc = cle.project_document(project, [], [], now=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC))
    assert doc["events"] == []


def test_project_document_emits_purl_identifier():
    project = SimpleNamespace(key="myproject", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    doc = cle.project_document(project, [], [], now=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC))
    assert doc["identifier"] == "pkg:sid/apache.org/the+asf/myproject"


def test_project_document_emits_schema_url():
    project = SimpleNamespace(key="example", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    doc = cle.project_document(project, [], [], now=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC))
    assert doc["$schema"] == cle.CLE_SCHEMA_URL


def test_project_document_omits_definitions_when_no_eod_or_eos_events():
    project = SimpleNamespace(key="example", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    rel = SimpleNamespace(
        version="1.0.0",
        key="example-1.0.0",
        cycle_key="example-default",
    )
    release_event = _event(
        event=sql.LifecycleEventType.RELEASE,
        effective=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        version_key="example-1.0.0",
        cycle_key="example-default",
        row_id=1,
    )
    eol = _event(
        event=sql.LifecycleEventType.EOL,
        effective=datetime.datetime(2027, 6, 1, tzinfo=datetime.UTC),
        cycle_key="example-default",
        row_id=2,
    )
    doc = cle.project_document(
        project, [release_event, eol], [rel], now=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    )
    assert "definitions" not in doc


def test_project_document_orders_events_by_id_descending():
    project = SimpleNamespace(key="example", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    rel = SimpleNamespace(version="1.0.0", key="example-1.0.0", cycle_key="example-default")
    older_published_higher_id = _event(
        event=sql.LifecycleEventType.RELEASE,
        effective=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        published=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        version_key="example-1.0.0",
        cycle_key="example-default",
        row_id=2,
    )
    newer_published_lower_id = _event(
        event=sql.LifecycleEventType.EOD,
        effective=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC),
        published=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
        cycle_key="example-default",
        row_id=1,
    )
    doc = cle.project_document(
        project,
        [newer_published_lower_id, older_published_higher_id],
        [rel],
        now=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
    )
    ids = [e["id"] for e in doc["events"]]
    assert ids == [2, 1]


def test_project_document_records_updated_at_from_now_param():
    project = SimpleNamespace(key="example", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    now = datetime.datetime(2026, 7, 1, 12, 0, 0, tzinfo=datetime.UTC)
    doc = cle.project_document(project, [], [], now=now)
    assert doc["updatedAt"] == "2026-07-01T12:00:00Z"


def test_project_document_renders_withdrawn_event_alongside_target():
    project = SimpleNamespace(key="example", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    rel = SimpleNamespace(version="1.0.0", key="example-1.0.0", cycle_key="example-default")
    target = _event(
        event=sql.LifecycleEventType.RELEASE,
        effective=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        version_key="example-1.0.0",
        cycle_key="example-default",
        row_id=1,
    )
    withdraw = _event(
        event=sql.LifecycleEventType.WITHDRAW,
        effective=datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC),
        version_key="example-1.0.0",
        cycle_key="example-default",
        target_event_id=1,
        row_id=2,
    )
    doc = cle.project_document(
        project, [target, withdraw], [rel], now=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    )
    types_emitted = {e["type"] for e in doc["events"]}
    assert types_emitted == {"released", "withdrawn"}
    withdrawn = next(e for e in doc["events"] if e["type"] == "withdrawn")
    assert withdrawn["eventId"] == 1


def test_release_document_renders_each_event_passed_in():
    project = SimpleNamespace(key="example", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    rel = SimpleNamespace(version="1.0.0", key="example-1.0.0", cycle_key="example-default")
    release_event = _event(
        event=sql.LifecycleEventType.RELEASE,
        effective=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        version_key="example-1.0.0",
        cycle_key="example-default",
        row_id=1,
    )
    eos_event = _event(
        event=sql.LifecycleEventType.EOS,
        effective=datetime.datetime(2026, 12, 1, tzinfo=datetime.UTC),
        cycle_key="example-default",
        row_id=2,
    )
    doc = cle.release_document(
        project,
        rel,
        [release_event, eos_event],
        now=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
    )
    types_emitted = {e["type"] for e in doc["events"]}
    assert types_emitted == {"released", "endOfSupport"}


def test_releases_by_cycle_groups_by_cycle_key():
    rels = [
        SimpleNamespace(version="1.0.0", cycle_key="example-1"),
        SimpleNamespace(version="1.1.0", cycle_key="example-1"),
        SimpleNamespace(version="2.0.0", cycle_key="example-2"),
    ]
    grouped = cle._releases_by_cycle(rels)
    assert set(grouped.keys()) == {"example-1", "example-2"}
    assert [r.version for r in grouped["example-1"]] == ["1.0.0", "1.1.0"]
    assert [r.version for r in grouped["example-2"]] == ["2.0.0"]


def test_releases_by_cycle_returns_empty_dict_for_no_releases():
    assert cle._releases_by_cycle([]) == {}


def test_render_event_archive_emits_versions_range():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    rel = SimpleNamespace(version="1.0.0", key="foo-1.0.0", cycle_key="foo-default")
    event = _event(
        event=sql.LifecycleEventType.ARCHIVE,
        effective=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
        version_key="foo-1.0.0",
        cycle_key="foo-default",
    )
    rendered = _render(project, event, {"foo-1.0.0": rel}, {"foo-default": [rel]})
    assert rendered["type"] == "endOfDistribution"
    assert rendered["versions"] == [{"range": "vers:generic/1.0.0"}]


def test_render_event_eod_uses_cycle_releases_for_range():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    rels = [
        SimpleNamespace(version="1.0.0", key="foo-1.0.0", cycle_key="foo-default"),
        SimpleNamespace(version="1.1.0", key="foo-1.1.0", cycle_key="foo-default"),
    ]
    event = _event(
        event=sql.LifecycleEventType.EOD,
        effective=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
        cycle_key="foo-default",
    )
    rendered = _render(project, event, {r.key: r for r in rels}, {"foo-default": rels})
    assert rendered["type"] == "endOfDevelopment"
    assert rendered["versions"] == [{"range": "vers:generic/1.0.0|1.1.0"}]
    assert rendered["supportId"] == "default"


def test_render_event_eol_for_semver_uses_derived_range():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SEMVER, committee_key="mycommittee")
    event = _event(
        event=sql.LifecycleEventType.EOL,
        effective=datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC),
        cycle_key="foo-2.x",
    )
    rendered = _render(project, event, {}, {})
    assert rendered["versions"] == [{"range": "vers:semver/>=2.0.0|<3.0.0"}]


def test_render_event_eol_omits_support_id():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    event = _event(
        event=sql.LifecycleEventType.EOL,
        effective=datetime.datetime(2027, 6, 1, tzinfo=datetime.UTC),
        cycle_key="foo-default",
    )
    rendered = _render(project, event, {}, {})
    assert rendered["type"] == "endOfLife"
    assert "supportId" not in rendered


def test_render_event_eol_raises_when_cycle_key_missing():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    event = _event(
        event=sql.LifecycleEventType.EOL,
        effective=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
    )
    with pytest.raises(ValueError, match="requires cycle_key"):
        _render(project, event, {}, {})


def test_render_event_eos_uses_wildcard_for_empty_cycle():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    event = _event(
        event=sql.LifecycleEventType.EOS,
        effective=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
        cycle_key="foo-default",
    )
    rendered = _render(project, event, {}, {})
    assert rendered["type"] == "endOfSupport"
    assert rendered["versions"] == [{"range": "vers:generic/*"}]
    assert rendered["supportId"] == "default"


def test_render_event_includes_references_when_set():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    rel = SimpleNamespace(version="1.0.0", key="foo-1.0.0", cycle_key="foo-default")
    event = _event(
        event=sql.LifecycleEventType.RELEASE,
        effective=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        version_key="foo-1.0.0",
        cycle_key="foo-default",
        reference_urls=["https://lists.apache.org/thread/abc", "https://example.com/announce"],
    )
    rendered = _render(project, event, {"foo-1.0.0": rel}, {"foo-default": [rel]})
    assert rendered["references"] == [
        "https://lists.apache.org/thread/abc",
        "https://example.com/announce",
    ]


def test_render_event_omits_references_when_empty():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    rel = SimpleNamespace(version="1.0.0", key="foo-1.0.0", cycle_key="foo-default")
    event = _event(
        event=sql.LifecycleEventType.RELEASE,
        effective=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        version_key="foo-1.0.0",
        cycle_key="foo-default",
    )
    rendered = _render(project, event, {"foo-1.0.0": rel}, {"foo-default": [rel]})
    assert "references" not in rendered


def test_render_event_release_emits_version_and_separate_dates():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    rel = SimpleNamespace(version="1.2.3", key="foo-1.2.3", cycle_key="foo-default")
    event = _event(
        event=sql.LifecycleEventType.RELEASE,
        effective=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        published=datetime.datetime(2025, 12, 1, tzinfo=datetime.UTC),
        version_key="foo-1.2.3",
        cycle_key="foo-default",
    )
    rendered = _render(project, event, {"foo-1.2.3": rel}, {"foo-default": [rel]})
    assert rendered["type"] == "released"
    assert rendered["effective"] == "2026-01-01T00:00:00Z"
    assert rendered["published"] == "2025-12-01T00:00:00Z"
    assert rendered["version"] == "1.2.3"
    assert rendered["license"] == "Apache-2.0"


def test_render_event_release_raises_when_release_unknown():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    event = _event(
        event=sql.LifecycleEventType.RELEASE,
        effective=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        version_key="foo-1.0.0",
    )
    with pytest.raises(ValueError, match="references unknown release"):
        _render(project, event, {}, {})


def test_render_event_release_raises_when_version_key_missing():
    project = SimpleNamespace(key="foo", version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    event = _event(
        event=sql.LifecycleEventType.RELEASE,
        effective=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    with pytest.raises(ValueError, match="requires version_key"):
        _render(project, event, {}, {})


def test_semver_bounds_for_major_minor_cycle_name():
    assert cle._semver_bounds_for_cycle_name("2.1") == ("2.1.0", "2.2.0")


def test_semver_bounds_for_major_only_cycle_name():
    assert cle._semver_bounds_for_cycle_name("2") == ("2.0.0", "3.0.0")


def test_semver_bounds_returns_none_for_empty_name():
    assert cle._semver_bounds_for_cycle_name("") is None


def test_semver_bounds_returns_none_for_non_numeric_name():
    assert cle._semver_bounds_for_cycle_name("default") is None


def test_semver_bounds_returns_none_for_wildcard_in_middle():
    assert cle._semver_bounds_for_cycle_name("2.x.0") is None


def test_semver_bounds_treats_trailing_x_as_wildcard():
    assert cle._semver_bounds_for_cycle_name("2.x") == ("2.0.0", "3.0.0")
    assert cle._semver_bounds_for_cycle_name("2.1.x") == ("2.1.0", "2.2.0")


def test_vers_literal_emits_single_version_constraint():
    project = SimpleNamespace(version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    assert cle._vers_literal(project, "1.0.0") == "vers:generic/1.0.0"


def test_vers_scheme_picks_generic_for_simple_projects():
    project = SimpleNamespace(version_method=sql.VersionMethod.SIMPLE, committee_key="mycommittee")
    assert cle._vers_scheme(project) == "generic"


def test_vers_scheme_picks_semver_for_semver_projects():
    project = SimpleNamespace(version_method=sql.VersionMethod.SEMVER, committee_key="mycommittee")
    assert cle._vers_scheme(project) == "semver"


def _event(
    *,
    event: sql.LifecycleEventType,
    effective: datetime.datetime,
    published: datetime.datetime | None = None,
    version_key: str | None = None,
    cycle_key: str | None = None,
    reference_urls: list[str] | None = None,
    target_event_id: int | None = None,
    row_id: int | None = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        event=event,
        effective=effective,
        published=published if published is not None else effective,
        version_key=version_key,
        cycle_key=cycle_key,
        reference_urls=reference_urls or [],
        target_event_id=target_event_id,
    )


def _render(project, event, releases_by_key, releases_by_cycle):
    typed = cle._to_cle_event(project, event, releases_by_key, releases_by_cycle)
    return cle_lib.event_to_dict(typed)
