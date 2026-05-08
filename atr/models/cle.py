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

"""ECMA-428 Common Lifecycle Enumeration data model.

Typed Pydantic models for CLE documents and events, plus a dict
renderer. Has no ATR-specific dependencies; Apache-specific concerns
(PURL identifier, license string, support policy id) live in the adapter
at `atr/cle.py`.

VERS ranges (ECMA-427) are held as opaque strings here. Resolving cycle
names to ranges or literal versions is an adapter responsibility.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

import pydantic

if TYPE_CHECKING:
    from collections.abc import Iterable

CLE_SCHEMA_URL: Final[str] = "https://ecma-tc54.github.io/ECMA-428/cle.v1.0.0.schema.json"


class EventBase(pydantic.BaseModel):
    """Fields shared by every CLE event type."""

    id: int
    effective: datetime.datetime
    published: datetime.datetime
    references: list[str] = pydantic.Field(default_factory=list)


class EndOfDevelopmentEvent(EventBase):
    type: Literal["endOfDevelopment"] = "endOfDevelopment"
    versions: list[str]
    support_id: str


class EndOfDistributionEvent(EventBase):
    type: Literal["endOfDistribution"] = "endOfDistribution"
    versions: list[str]


class EndOfLifeEvent(EventBase):
    type: Literal["endOfLife"] = "endOfLife"
    versions: list[str]


class EndOfSupportEvent(EventBase):
    type: Literal["endOfSupport"] = "endOfSupport"
    versions: list[str]
    support_id: str


class ReleasedEvent(EventBase):
    type: Literal["released"] = "released"
    version: str
    license: str | None = None


class WithdrawnEvent(EventBase):
    type: Literal["withdrawn"] = "withdrawn"
    event_id: int
    reason: str | None = None


CleEvent = Annotated[
    EndOfDevelopmentEvent
    | EndOfDistributionEvent
    | EndOfLifeEvent
    | EndOfSupportEvent
    | ReleasedEvent
    | WithdrawnEvent,
    pydantic.Field(discriminator="type"),
]


class SupportDefinition(pydantic.BaseModel):
    id: str
    description: str
    url: str | None = None


class CleDocument(pydantic.BaseModel):
    """
    A CLE document as defined by ECMA-428.

    `identifier` may be a single PURL or an array of aliases for the same
    component.
    """

    schema_url: str = CLE_SCHEMA_URL
    identifier: str | list[str]
    updated_at: datetime.datetime
    events: list[CleEvent] = pydantic.Field(default_factory=list)
    definitions: dict[str, list[SupportDefinition]] | None = None

    @classmethod
    def from_events(
        cls,
        *,
        identifier: str | list[str],
        events: Iterable[CleEvent],
        definitions: dict[str, list[SupportDefinition]] | None = None,
        now: datetime.datetime,
    ) -> CleDocument:
        """
        Build a document from a flat iterable of events.

        `updated_at` is taken from the latest `published` across the events,
        falling back to `now` so the field is always populated.
        """
        event_list = list(events)
        updated = max((e.published for e in event_list), default=now)
        return cls(
            identifier=identifier,
            updated_at=updated,
            events=event_list,
            definitions=definitions,
        )

    def to_dict(self) -> dict[str, Any]:
        """Render to a dict."""
        # Per ECMA-428 § 6.2, events emit in descending id order.
        sorted_events = sorted(self.events, key=lambda e: e.id, reverse=True)
        doc: dict[str, Any] = {
            "$schema": self.schema_url,
            "identifier": self.identifier,
            "updatedAt": _iso(self.updated_at),
            "events": [event_to_dict(e) for e in sorted_events],
        }
        if self.definitions:
            doc["definitions"] = {
                key: [d.model_dump(exclude_none=True) for d in defs] for key, defs in self.definitions.items()
            }
        return doc


def event_to_dict(event: CleEvent) -> dict[str, Any]:
    """Render one event to a dict."""
    rendered: dict[str, Any] = {
        "id": event.id,
        "type": event.type,
        "effective": _iso(event.effective),
        "published": _iso(event.published),
    }

    if isinstance(event, ReleasedEvent):
        rendered["version"] = event.version
        if event.license is not None:
            rendered["license"] = event.license
    elif isinstance(event, EndOfDevelopmentEvent | EndOfSupportEvent):
        rendered["versions"] = [{"range": v} for v in event.versions]
        rendered["supportId"] = event.support_id
    elif isinstance(event, EndOfLifeEvent | EndOfDistributionEvent):
        rendered["versions"] = [{"range": v} for v in event.versions]
    elif isinstance(event, WithdrawnEvent):
        rendered["eventId"] = event.event_id
        if event.reason is not None:
            rendered["reason"] = event.reason

    if event.references:
        rendered["references"] = list(event.references)

    return rendered


def _iso(value: datetime.datetime) -> str:
    """Render a UTC datetime as ISO 8601 with a Z suffix."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    else:
        value = value.astimezone(datetime.UTC)
    return value.isoformat().replace("+00:00", "Z")
