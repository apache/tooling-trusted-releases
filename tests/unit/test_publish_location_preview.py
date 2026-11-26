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

import contextlib
import unittest.mock as mock

import pytest
import quart

import atr.config as config
import atr.construct as construct
import atr.errors as errors
import atr.get.start as start
import atr.models.sql as sql


@pytest.fixture(autouse=True)
def release_target(monkeypatch):
    monkeypatch.setattr(config.get(), "SVN_DIST_PUBLIC_URL", "https://dist.apache.org/repos/dist/release")


@pytest.fixture
def preview(monkeypatch):
    app = quart.Quart(__name__)
    app.config["TESTING"] = True
    app.register_error_handler(errors.ASFQuartException, lambda error: ("", error.errorcode))
    app.register_blueprint(start.get._BLUEPRINT)
    data = mock.Mock()
    data.project.return_value.demand = mock.AsyncMock()
    session = mock.Mock()
    session.prevent_confusing_ui_display = mock.AsyncMock()
    monkeypatch.setattr(start.db, "session", lambda: contextlib.nullcontext(data))
    monkeypatch.setattr(start.get.auth.session, "read", mock.AsyncMock(return_value=object()))
    monkeypatch.setattr(start.get.common, "authenticate", mock.AsyncMock(return_value=session))
    monkeypatch.setattr(start.get.log, "performance", mock.Mock())
    monkeypatch.setattr(start.construct, "publish_location_mismatch", mock.AsyncMock(return_value=None))
    return app.test_client(), data, session


def _project(*, key: str, committee_key: str, policy: str, podling: bool = False) -> sql.Project:
    committee = sql.Committee(key=committee_key, is_podling=podling)
    return sql.Project(
        key=key,
        committee_key=committee_key,
        committee=committee,
        release_policy=sql.ReleasePolicy(download_path_suffix=policy),
    )


def _location(
    project: sql.Project, version: str, entered: str, *, at_start: bool, at_policy: bool = False
) -> str | None:
    if project.committee is None:
        raise ValueError("The test project has no committee")
    return construct.publish_location_preview(
        project, project.committee, version, entered, at_start=at_start, at_policy=at_policy
    )


@pytest.mark.parametrize(
    ("key", "policy", "entered", "at_start", "expected"),
    [
        # Left as the project's default, a top level project publishes into its committee directory
        ("maven", "", "", True, "dist/release/maven/"),
        # and any other project into a directory of its own
        ("maven-surefire", "", "", True, "dist/release/maven/maven-surefire-3.9.10/"),
        (
            "maven",
            "maven-{{MAJOR_VERSION}}/{{VERSION}}",
            "maven-{{MAJOR_VERSION}}/{{VERSION}}",
            True,
            "dist/release/maven/maven-3/3.9.10/",
        ),
        # Cleared when the project has a default, it's the committee directory
        ("maven-surefire", "surefire/{{VERSION}}", "", True, "dist/release/maven/"),
        ("maven", "", "{{PROJECT_KEY}}/{{VERSION}}", True, "dist/release/maven/maven/3.9.10/"),
        # Past the start form an empty location is always the committee directory
        ("maven-surefire", "", "", False, "dist/release/maven/"),
        ("maven-surefire", "", "surefire/3.9.10", False, "dist/release/maven/surefire/3.9.10/"),
    ],
)
def test_location(key, policy, entered, at_start, expected) -> None:
    project = _project(key=key, committee_key="maven", policy=policy)

    assert _location(project, "3.9.10", entered, at_start=at_start) == expected


def test_location_podling_is_under_incubator() -> None:
    project = _project(key="amoro", committee_key="amoro", policy="{{VERSION}}", podling=True)

    assert _location(project, "0.8.0", "{{VERSION}}", at_start=True) == "dist/release/incubator/amoro/0.8.0/"


@pytest.mark.parametrize(
    ("key", "entered", "expected"),
    [
        ("maven", "", "dist/release/maven/"),
        ("maven-surefire", "", "dist/release/maven/maven-surefire-3.9.10/"),
        ("maven-surefire", "surefire/{{VERSION}}", "dist/release/maven/surefire/3.9.10/"),
    ],
)
def test_location_policy(key, entered, expected) -> None:
    project = _project(key=key, committee_key="maven", policy="surefire/{{VERSION}}")

    assert _location(project, "3.9.10", entered, at_start=False, at_policy=True) == expected


@pytest.mark.parametrize(("version", "entered"), [("", "x"), ("1.2.", "x"), ("1.0", "../x"), ("1.0", "/x")])
def test_location_unusable_input(version, entered) -> None:
    project = _project(key="maven", committee_key="maven", policy="")

    assert _location(project, version, entered, at_start=True) is None


@pytest.mark.parametrize(
    ("location", "previous", "expected"),
    [
        # The last release has a versioned directory, and this one would land in the committee directory
        (
            "dist/release/maven/",
            ("3.9.9", "maven/maven-3/3.9.9"),
            ("3.9.9", "dist/release/maven/maven-3/3.9.9/"),
        ),
        ("dist/release/maven/maven-3/3.9.10/", ("3.9.9", "maven/maven-3/3.9.9"), None),
        # A catalogued file in a subdirectory of its release is the same layout
        ("dist/release/maven/maven-3/3.9.10/", ("3.9.9", "maven/maven-3/3.9.9/binaries"), None),
        ("dist/release/maven/", ("3.9.9", "maven"), None),
        ("dist/release/maven/3.9.10/", ("3.9.9", "maven"), ("3.9.9", "dist/release/maven/")),
        ("dist/release/maven/", None, None),
        (None, ("3.9.9", "maven"), None),
    ],
)
async def test_mismatch(monkeypatch, location, previous, expected) -> None:
    lookup = mock.AsyncMock(return_value=previous)
    monkeypatch.setattr(construct.interaction, "latest_catalogued_directory", lookup)

    assert await construct.publish_location_mismatch("maven", "3.9.10", location) == expected


async def test_mismatch_including_version(monkeypatch) -> None:
    lookup = mock.AsyncMock(return_value=("3.9.10", "maven/3.9.10"))
    monkeypatch.setattr(construct.interaction, "latest_catalogued_directory", lookup)

    location = "dist/release/maven/maven-3/3.9.10/"
    mismatch = await construct.publish_location_mismatch("maven", "3.9.10", location, include_version=True)

    assert mismatch == ("3.9.10", "dist/release/maven/3.9.10/")
    lookup.assert_awaited_once_with("maven", "")


@pytest.mark.parametrize(
    ("key", "entered", "expected"),
    [
        ("maven", "", "dist/release/maven/"),
        ("maven-surefire", "", "dist/release/maven/maven-surefire-{{VERSION}}/"),
        ("maven-surefire", "surefire/{{VERSION}}/", "dist/release/maven/surefire/{{VERSION}}/"),
        ("maven", "{{PROJECT_KEY}}-{{MAJOR_VERSION}}", "dist/release/maven/maven-{{MAJOR_VERSION}}/"),
        ("maven", "/x", None),
    ],
)
def test_pattern(key, entered, expected) -> None:
    project = _project(key=key, committee_key="maven", policy=entered)
    if project.committee is None:
        raise ValueError("The test project has no committee")

    assert construct.publish_location_pattern(project, project.committee, entered) == expected


@pytest.mark.parametrize(
    ("committee_key", "podling", "entered", "expected"),
    [
        ("maven", False, "maven/maven-3/{{VERSION}}", ("maven/", "maven-3/{{VERSION}}")),
        ("maven", False, " dist/release/maven/maven-3/3.9.10", ("dist/release/maven/", "maven-3/3.9.10")),
        ("maven", False, "release/maven/3.9.10", ("release/maven/", "3.9.10")),
        ("amoro", True, "incubator/amoro/{{VERSION}}", ("incubator/amoro/", "{{VERSION}}")),
        ("amoro", True, "amoro/{{VERSION}}", ("amoro/", "{{VERSION}}")),
        ("maven", False, "maven-3/{{VERSION}}", None),
        ("maven", False, "maven/", None),
        ("maven", False, "", None),
    ],
)
def test_repeat(committee_key, podling, entered, expected) -> None:
    committee = sql.Committee(key=committee_key, is_podling=podling)

    assert construct.publish_location_repeat(committee, entered) == expected


async def test_route_missing_project(preview) -> None:
    client, data, _session = preview
    data.project.return_value.demand.side_effect = errors.ASFQuartException("Missing project", errorcode=404)

    response = await client.get("/start/example/location-preview?version=1.0")

    assert response.status_code == 404


async def test_route_policy_mode_shows_the_pattern(preview) -> None:
    client, data, _session = preview
    data.project.return_value.demand.return_value = _project(key="maven-surefire", committee_key="maven", policy="")

    response = await client.get(
        "/start/maven-surefire/location-preview", query_string={"version": "1.0", "mode": "policy"}
    )

    assert (await response.get_json())["location"] == "dist/release/maven/maven-surefire-{{VERSION}}/"
    start.construct.publish_location_mismatch.assert_awaited_once_with(
        "maven-surefire", "1.0", "dist/release/maven/maven-surefire-1.0/", include_version=True
    )


async def test_route_reports_problems(preview) -> None:
    client, data, _session = preview
    data.project.return_value.demand.return_value = _project(key="maven", committee_key="maven", policy="")

    repeated = await client.get(
        "/start/maven/location-preview", query_string={"version": "3.9.10", "suffix": "maven/maven-3/{{VERSION}}"}
    )
    invalid = await client.get("/start/maven/location-preview", query_string={"version": "3.9.10", "suffix": "/x"})

    repeated_json = await repeated.get_json()
    invalid_json = await invalid.get_json()
    assert (repeated_json["repeated"], repeated_json["fixed"]) == ("maven/", "maven-3/{{VERSION}}")
    assert invalid_json["location"] is None
    assert invalid_json["error"] == "Publish location is not a valid path: Absolute paths are not allowed"


async def test_route_resolves(preview) -> None:
    client, data, _session = preview
    data.project.return_value.demand.return_value = _project(key="maven", committee_key="maven", policy="")

    response = await client.get(
        "/start/maven/location-preview", query_string={"version": "3.9.10", "suffix": "maven-3/{{VERSION}}"}
    )

    assert response.status_code == 200
    assert await response.get_json() == {
        "location": "dist/release/maven/maven-3/3.9.10/",
        "previous_version": None,
        "previous_location": None,
        "error": None,
        "repeated": None,
        "fixed": None,
        "shown": True,
    }


async def test_route_start_mode_defers_to_default(preview) -> None:
    client, data, _session = preview
    data.project.return_value.demand.return_value = _project(key="maven-surefire", committee_key="maven", policy="")

    started = await client.get(
        "/start/maven-surefire/location-preview", query_string={"version": "1.0", "mode": "start"}
    )
    published = await client.get("/start/maven-surefire/location-preview", query_string={"version": "1.0"})

    assert (await started.get_json())["location"] == "dist/release/maven/maven-surefire-1.0/"
    assert (await published.get_json())["location"] == "dist/release/maven/"


async def test_route_unauthorized(preview) -> None:
    client, data, session = preview
    session.prevent_confusing_ui_display.side_effect = errors.ASFQuartException("Forbidden", errorcode=403)

    response = await client.get("/start/example/location-preview?version=1.0")

    assert response.status_code == 403
    data.project.assert_not_called()


@pytest.mark.parametrize(
    ("entered", "location", "mismatched", "expected"),
    [
        ("maven-3/{{VERSION}}", "dist/release/maven/maven-3/3.9.10/", False, True),
        ("maven-3/3.9.10", "dist/release/maven/maven-3/3.9.10/", False, False),
        (" maven-3/3.9.10/", "dist/release/maven/maven-3/3.9.10/", False, False),
        ("", "dist/release/maven/", False, False),
        ("", "dist/release/maven/maven-surefire-3.9.10/", False, True),
        ("maven-3/3.9.10", "dist/release/maven/maven-3/3.9.10/", True, True),
        ("maven-3/3.9.10", None, False, False),
        ("maven-3/{{VERSION}}", None, False, True),
        ("", None, False, True),
    ],
)
def test_shown(entered, location, mismatched, expected) -> None:
    mismatch = ("3.9.9", "dist/release/maven/3.9.9/") if mismatched else None

    shown = construct.publish_location_shown(
        "dist/release/maven/", entered, location, error=None, repeat=None, mismatch=mismatch
    )

    assert shown is expected
