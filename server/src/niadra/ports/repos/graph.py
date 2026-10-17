"""The relationship graph's read model (5.22): how customers, companies, objects, agents and systems relate,
as counts and ids, over the tables their modules own (4.3)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.records import AgentAction


@dataclass(frozen=True, slots=True)
class Tie:
    """A link or an open identity suggestion, with the profile on each side now.

    `kind` is `link` (`a` the person's handle, `b` the organization's) or `suggestion` (the pair)."""

    kind: str
    tie_id: UUID
    a: UUID
    b: UUID
    a_profile: UUID | None
    b_profile: UUID | None
    since: datetime
    role: str | None = None
    score: float | None = None


@dataclass(frozen=True, slots=True)
class Talk:
    """A source's sessions with a profile: conversations and tasks."""

    source_id: UUID
    profile_id: UUID
    count: int
    last_at: datetime
    channels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Acts:
    """A source's actions on an object, or on a handle (an action on no object that closes its promise, or,
    by owner, every action on the objects the handle owns). `latest` is the newest of them, without its
    result: what the reader's policy decides on."""

    source_id: UUID
    object_id: UUID | None
    handle_id: UUID | None
    count: int
    last_at: datetime
    operations: tuple[str, ...]
    latest: AgentAction


@dataclass(frozen=True, slots=True)
class Reports:
    """A source's system events about an object, or, by owner, about the objects a handle owns."""

    source_id: UUID
    object_id: UUID | None
    handle_id: UUID | None
    count: int
    last_at: datetime


@dataclass(frozen=True, slots=True)
class Handoffs:
    """A source's handoffs of one subject to one target: how many, and the latest one's status and outcome."""

    source_id: UUID
    subject_hmac: bytes
    target: str
    count: int
    last_at: datetime
    status: str
    outcome: str | None


@dataclass(frozen=True, slots=True)
class Activity:
    talks: list[Talk] = field(default_factory=list)
    acts: list[Acts] = field(default_factory=list)
    reports: list[Reports] = field(default_factory=list)
    handoffs: list[Handoffs] = field(default_factory=list)
    owned: dict[UUID, int] = field(default_factory=dict)
    """By owner: how many objects each handle owns."""
    involved: dict[UUID, int] = field(default_factory=dict)
    """How many objects each customer of `customers` owns or takes part in, each object once."""


class GraphRepository(Protocol):
    """Owned by the `graph` module, which writes nothing: each method is one statement over the identity,
    sessions, objects, ingest and coordination tables, bounded by the ids it is given."""

    async def ties(self, handle_ids: Sequence[UUID]) -> list[Tie]:
        """The links in force and the open suggestions where either side is one of the handles."""
        ...

    async def activity(
        self,
        *,
        profile_ids: Sequence[UUID] = (),
        object_ids: Sequence[UUID] = (),
        handle_ids: Sequence[UUID] = (),
        subject_hmacs: Sequence[bytes] = (),
        owner_handle_ids: Sequence[UUID] = (),
        customers: Mapping[UUID, Sequence[UUID]] | None = None,
    ) -> Activity:
        """Sessions per (source, profile) of `profile_ids`; actions and system events per (source, object) of
        `object_ids`; actions on no object that close a promise of `handle_ids`; handoffs per (source,
        subject, target) of `subject_hmacs`; and, for `owner_handle_ids`, per (source, handle) the actions
        and system events on the objects each owns, and how many it owns; for `customers` (each profile with
        its handles), how many objects each owns or takes part in."""
        ...
