"""What the `signals` module offers other modules (24.3.2).

- `SignalOverlay`: the session overlay, interactions not derived yet, per handle, in the state store, written
  when a turn or a batch item is applied so the next turn of any agent sees them. Losing it loses no data:
  every entry is also pending in the database until `SignalsService.derive_due` folds it.
- `InteractionSink`: interactions another module learned outside any turn record (a handoff's outcome that
  declined a network becomes a preference), taken as a standalone interaction batch is.
- `TurnCounterStore`: what each recorded turn cost, per conversation and per case, in the state store, for
  the context budget a read shows without the database.
- `ObjectInstructions`: the instructions people said about an object, kept as `Stated`
  entries with `scope: object` in the signals of who said them: written by the memory module in its
  reconcile transaction, read by the compiler for the participants of the objects it compiles.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.contracts.events import InteractionItem
from niadra.ports.uow import UnitOfWork


class SignalOverlay(Protocol):
    async def add(self, space_id: UUID, entries: Mapping[UUID, Mapping[str, bytes]], ttl_s: int) -> None:
        """Sealed entries by handle and entry key. An entry already folded (`drop`) is not added again."""
        ...

    async def read(self, space_id: UUID, handle_ids: Sequence[UUID]) -> dict[UUID, dict[str, bytes]]: ...

    async def drop(self, space_id: UUID, handle_id: UUID, entry_keys: Sequence[str], ttl_s: int) -> None:
        """Removes folded entries and remembers them for `ttl_s`: applied again late, they stay out."""
        ...

    async def purge(self, space_id: UUID, handle_ids: Sequence[UUID]) -> None: ...

    async def whole(self) -> bool:
        """Whether the store still holds every entry written to it: False once it came back empty."""
        ...

    async def claim_rebuild(self, token: str, lease_ms: int) -> bool:
        """One rebuilder at a time, for `lease_ms`; False when the store is whole or another holds it."""
        ...

    async def seal(self, token: str) -> bool:
        """Marks the store whole again, if the rebuild is still this token's."""
        ...


class TurnCounterStore(Protocol):
    async def add(self, space_id: UUID, counts: Mapping[str, Mapping[str, str]], ttl_s: int) -> None:
        """Per key (a conversation's or a case's keyed digest), each turn's counts by `<source>:<turn>`:
        writing the same turn again changes nothing. Every key written lives `ttl_s` more."""
        ...

    async def read(self, space_id: UUID, keys: Sequence[str]) -> list[dict[str, str]]: ...

    async def forget(self, space_id: UUID, keys: Sequence[str]) -> None: ...


class InteractionSink(Protocol):
    async def record(self, space_id: UUID, source_id: UUID, items: Sequence[InteractionItem]) -> None:
        """Kept sealed in object storage and handed to `apply_interactions` by one outbox row, in a
        transaction of its own: one statement. Nothing is kept in a space with `signals` off; a duplicate by
        `idempotency_key` is applied once."""
        ...


class NoInteractions:
    """The sink of a service built without the `signals` module: it records nothing."""

    async def record(self, space_id: UUID, source_id: UUID, items: Sequence[InteractionItem]) -> None:  # noqa: ARG002
        return None


@dataclass(frozen=True, slots=True)
class SaidHours:
    """The local hours a person said they may be contacted in, on a channel (None: every channel), kept as
    their contact window (`contact_window.<channel or any>`, the latest said wins); an empty `window` lifts
    it."""

    handle_id: UUID
    key: str
    at: datetime
    channel: str | None
    window: str
    """`Window.encode`, or empty for a lift."""
    event_key: str | None = None


@dataclass(frozen=True, slots=True)
class SaidInstruction:
    """An instruction one person said about one object, as the extraction found it."""

    handle_id: UUID
    key: str
    """Stable across extractions of the same turns: saying it again replaces it, never adds a second one."""
    at: datetime
    object: str | None
    """`type:namespace:id`; None for an instruction about an organization."""
    text: str
    strength: str
    audience: str
    attr: str | None = None
    event_key: str | None = None
    about: str | None = None
    """The handle of the organization an instruction about no object is about."""
    speaker: str | None = None
    """`human_agent` when the company's own person said it (`domain.memory.bound.said_by`)."""
    only_with: str | None = None
    """The one person it holds with, as the turns name them."""
    contact: bool = False
    """A contact preference: how or when `only_with` wants to be reached, never what may be said."""


@dataclass(frozen=True, slots=True)
class HeldInstruction:
    """An instruction in force in the signals of one handle, derived or still pending."""

    handle_id: UUID
    key: str
    object: str | None
    text: str
    strength: str
    said_at: datetime
    audience: str | None
    attr: str | None = None
    expires_at: datetime | None = None
    about: str | None = None
    speaker: str | None = None
    only_with: str | None = None
    contact: bool = False


class ObjectInstructions(Protocol):
    async def instruct(self, uow: UnitOfWork, space_id: UUID, said: Sequence[SaidInstruction]) -> int:
        """Pending in the caller's transaction (one statement) and in the overlay, so the next compilation
        reads them before they are derived. Returns how many were kept: none in a space with `signals` off."""
        ...

    async def hours(self, uow: UnitOfWork, space_id: UUID, said: Sequence[SaidHours]) -> int:
        """The person's contact windows, pending as `instruct` keeps instructions."""
        ...

    async def held(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        handle_ids: Sequence[UUID],
        objects: Sequence[str],
        abouts: Sequence[str] = (),
    ) -> list[HeldInstruction]:
        """The instructions about `objects`, or about the organizations of `abouts` (handle ids), the handles
        said, derived or pending, not expired: one statement in the caller's transaction and one overlay
        read. None for a subject who objected to personalization, nor in a space with `signals` off."""
        ...


class NoObjectInstructions:
    """The port of a service built without the `signals` module: no instruction is held."""

    async def instruct(self, uow: UnitOfWork, space_id: UUID, said: Sequence[SaidInstruction]) -> int:  # noqa: ARG002
        return 0

    async def hours(self, uow: UnitOfWork, space_id: UUID, said: Sequence[SaidHours]) -> int:  # noqa: ARG002
        return 0

    async def held(
        self,
        uow: UnitOfWork,  # noqa: ARG002
        space_id: UUID,  # noqa: ARG002
        handle_ids: Sequence[UUID],  # noqa: ARG002
        objects: Sequence[str],  # noqa: ARG002
        abouts: Sequence[str] = (),  # noqa: ARG002
    ) -> list[HeldInstruction]:
        return []
