"""Business objects: `GET /v1/objects/{type}/{namespace}/{id}` and `/timeline` (7.1) on the read API, and
a profile's objects with their agent actions in the Console (`GET /v1/profiles/{profile_id}/objects`)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from niadra.contracts.common import Model, ObjectRef
from niadra.contracts.context import HistoryItem
from niadra.contracts.memory import Origin
from niadra.contracts.state import ObjectMemoryOut
from niadra.domain.vocabulary import ActionStatus, Audience


class ObjectTimeline(Model):
    ref: ObjectRef
    items: list[HistoryItem]
    """System events and agent actions, newest first; one line each, never conversation content."""
    next_cursor: str | None = None
    as_of: datetime | None = None


class ActionOut(Model):
    """What an agent did in a system for the customer (18.2), with its reconciliation status (5.8)."""

    id: str
    operation: str
    status: ActionStatus
    result: str | None = Field(description="None when absent or masked for this reader.")
    masked: bool = False
    purpose: str | None = None
    occurred_at: datetime
    object_id: str | None = None
    closes_item_id: str | None = None
    confirmed_by_event_id: str | None = None
    origin: Origin


class ProfileObject(Model):
    id: str
    ref: ObjectRef
    role: Literal["owner", "participant"]
    state: dict[str, Any] | None = Field(description="None when the policy masks it for this reader.")
    masked: bool = False
    as_of: datetime
    source_id: str
    record_ref: str | None = None
    actions: list[ActionOut] = Field(default_factory=list, description="Newest first.")
    unconfirmed_link: bool = Field(
        default=False,
        description="It came through an identifier joined to the profile by a claim nobody confirmed yet "
        "(`HandleOut.provisional`). Only the `admin` and `security` roles see such items; every other "
        "reader, agents included, gets the profile without them until the link is confirmed.",
    )
    memory: ObjectMemoryOut | None = Field(
        default=None,
        description="What its owner and participants said about it: observations, instructions and open "
        "items, each with who said it and when.",
    )


class ProfileObjects(Model):
    profile_id: str
    policy_version: str
    audience: Audience
    objects: list[ProfileObject] = Field(default_factory=list)
    objects_next: str | None = Field(
        default=None,
        description="Where the objects' next page starts (`objects_cursor`); null when there are no more.",
    )
    actions: list[ActionOut] = Field(
        default_factory=list, description="Actions on no object of the list, e.g. one that closes a promise."
    )
    actions_next: str | None = Field(
        default=None,
        description="Where the actions' next page starts (`actions_cursor`); null when there are no more.",
    )
    withheld: dict[str, int] = Field(default_factory=dict)
