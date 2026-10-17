"""What coordination needs besides its tables (24.3.4): the state store that decides, and the confirmed queue
that carries each decision to the batch writer before the check answers."""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.contracts.events import EventItem
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.coordinate.model import (
    Admission,
    Claim,
    ClaimKind,
    ClaimResult,
    ClaimVia,
    EffectAttempt,
    EffectSnapshot,
    EffectState,
    Settler,
    StoreLife,
    SubjectCheck,
)
from niadra.domain.coordinate.records import CoordinationRecord
from niadra.domain.coordinate.window import Window, WindowedSuppression
from niadra.domain.vocabulary import EventKind


@dataclass(frozen=True, slots=True)
class ClaimSlot:
    """Where a claim lives in the state store, kept under its id so a release by id finds it."""

    key: str
    slot: str


@dataclass(frozen=True, slots=True)
class CommitmentEntry:
    """A commitment in the subject's list of the state store; `active` while it holds."""

    commitment_id: str
    type: str
    compatibility: str
    until_ms: int
    score: float | None = None
    agent: str | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class BudgetSpend:
    """A reservation the durable record holds, loaded back into its budget's window (and its slice's) after
    the state store came back empty."""

    reservation_id: str
    budget: str
    slice: str | None
    units: int
    at_ms: int
    until_ms: int


class CoordinationStore(Protocol):
    """The state store's scripts (`adapters/outbound/redis/coordinate.py`); every call raises
    `CacheUnavailable` when the store does not answer."""

    def claims_key(self, space_id: UUID, tag: str) -> str: ...

    def effect_key(self, space_id: UUID, key_hash: str) -> str: ...

    def idempotency_key(self, space_id: UUID, digest: str) -> str: ...

    async def life(self, *, now_ms: int) -> StoreLife: ...

    async def claim_rebuild(self, token: str, lease_ms: int) -> bool: ...

    async def extend_rebuild(self, token: str, lease_ms: int) -> bool: ...

    async def seal(self, life: StoreLife) -> bool: ...

    async def claim(
        self,
        key: str,
        slot: str,
        *,
        rank: int,
        level: str,
        holder: str,
        kind: ClaimKind,
        via: ClaimVia,
        until_ms: int,
        claim_id: str,
        intents: Sequence[str] = (),
        since_ms: int | None = None,
        now_ms: int,
    ) -> ClaimResult: ...

    async def end_observation(
        self, key: str, slot: str, *, at_ms: int, keep_until_ms: int, now_ms: int
    ) -> ClaimResult: ...

    async def locks(self, key: str, *, now_ms: int) -> list[Claim]: ...

    async def commit(
        self, space_id: UUID, tag: str, external_id: str, commitment: CommitmentEntry, *, now_ms: int
    ) -> tuple[bool, list[str]]: ...

    async def uncommit(self, space_id: UUID, tag: str, external_id: str, *, now_ms: int) -> str | None: ...

    async def commitments(
        self, space_id: UUID, tag: str, *, now_ms: int
    ) -> list[tuple[str, CommitmentEntry]]: ...

    async def budgets_used(
        self, space_id: UUID, tag: str, windows: Sequence[tuple[str, int]], *, now_ms: int
    ) -> list[int]: ...

    async def purge(self, space_id: UUID, tag: str) -> int:
        """Everything the store keeps of a target but its suppressions."""
        ...

    async def suppressed(self, space_id: UUID, tag: str, *, now_ms: int) -> list[str]:
        """The purposes the subject is suppressed for now at every hour, on any channel."""

    async def windows(self, space_id: UUID, tag: str, *, now_ms: int) -> list[WindowedSuppression]:
        """The subject's suppressions in force that hold only in their local hours."""
        ...

    async def release(self, key: str, slot: str, epoch: int, *, now_ms: int) -> ClaimResult: ...

    async def owner(self, key: str, *, now_ms: int) -> Claim | None: ...

    async def index_claim(self, space_id: UUID, claim_id: UUID, where: ClaimSlot, ttl_ms: int) -> None: ...

    async def find_claim(self, space_id: UUID, claim_id: UUID) -> ClaimSlot | None: ...

    async def check_subject(
        self,
        space_id: UUID,
        tag: str,
        reservation_id: str,
        *,
        direction: str,
        purpose: str,
        channel: str | None,
        agent: str,
        intent: str,
        units: int,
        budget: str,
        limit: int | None,
        window_ms: int,
        quiet_until_ms: int | None,
        slice_budget: str | None = None,
        slice_limit: int | None = None,
        spacing_ms: int = 0,
        blocked_by: str | None = None,
        blocked_until_ms: int | None = None,
        blocked_holder: str | None = None,
        window_until_ms: int | None = None,
        now_ms: int,
    ) -> SubjectCheck: ...

    async def spend(
        self,
        space_id: UUID,
        tag: str,
        budget: str,
        reservation_id: str,
        *,
        units: int,
        limit: int,
        window_ms: int,
        now_ms: int,
    ) -> Admission: ...

    async def settle_units(
        self, space_id: UUID, tag: str, budget: str, reservation_id: str, units: int | None
    ) -> bool: ...

    async def paid_max(self, space_id: UUID, operation: str) -> int | None: ...

    async def raise_paid_max(self, space_id: UUID, operation: str, units: int) -> None: ...

    async def suppress(
        self,
        space_id: UUID,
        tag: str,
        field: str,
        *,
        until_ms: int | None,
        suppression_id: UUID,
        now_ms: int,
        window: Window | None = None,
    ) -> UUID: ...

    async def lift(self, space_id: UUID, tag: str, field: str, *, now_ms: int) -> UUID | None: ...

    async def peek_effect(self, key: str, *, now_ms: int) -> tuple[EffectSnapshot | None, bool]: ...

    async def reserve_effect(
        self,
        key: str,
        *,
        kind: str,
        source: str,
        agent: str = "",
        cost_units: int = 0,
        fact: str = "",
        lease_ms: int,
        ttl_ms: int | None,
        now_ms: int,
    ) -> EffectAttempt: ...

    async def cancel_effect(self, key: str, attempt: int) -> bool: ...

    async def settle_effect(
        self, key: str, state: EffectState, *, attempt: int | None, by: Settler, now_ms: int
    ) -> tuple[str, EffectSnapshot | None, bool]: ...

    async def record_effect(
        self,
        key: str,
        state: EffectState,
        *,
        kind: str,
        source: str,
        agent: str,
        ttl_ms: int | None,
        now_ms: int,
    ) -> tuple[EffectSnapshot, bool]: ...

    async def load_effect(self, key: str, snapshot: EffectSnapshot, ttl_ms: int | None) -> bool: ...

    async def load_target(
        self,
        space_id: UUID,
        tag: str,
        life: StoreLife,
        *,
        claims: Mapping[str, Claim],
        suppressions: Mapping[str, str],
        spends: Sequence[BudgetSpend] = (),
        commitments: Mapping[str, CommitmentEntry] | None = None,
        now_ms: int,
    ) -> bool: ...

    async def remember(self, key: str, value: bytes, ttl_ms: int) -> bool: ...

    async def overwrite(self, key: str, value: bytes, ttl_ms: int) -> None: ...

    async def recall(self, key: str) -> bytes | None: ...

    async def forget(self, key: str) -> None: ...


class CoordinationLog(Protocol):
    """The confirmed queue (`niadra.coordination`): True once the broker holds the records on disk, False
    when it did not confirm within `timeout_s`."""

    async def publish(self, records: Sequence[CoordinationRecord], timeout_s: float) -> bool: ...


class CoordinationBatch(Protocol):
    records: list[CoordinationRecord]

    async def ack(self) -> None: ...

    async def retry_later(self) -> None: ...


class CoordinationFeed(Protocol):
    def batches(self, max_items: int) -> AsyncIterator[CoordinationBatch]: ...


COORDINATION_ACTIONS = ("commitment.", "handoff.")


def owned_by_coordination(event: EventItem) -> bool:
    """What coordination may record in a subject's memory: the actions of its commitments
    (`commitment.<type>`) and of its handoffs' outcomes (`handoff.outcome`), nothing else."""
    if event.kind is not EventKind.ACTION or event.action is None:
        return False
    return event.action.operation.startswith(COORDINATION_ACTIONS)


class SystemEvents(Protocol):
    """The ingest module takes coordination's own actions into the subject's memory, as the caller's, though
    its key holds `coordinate` and not `track` or `act`; any other event is refused."""

    async def record_coordination_events(
        self, caller: SourcePrincipal, events: Sequence[EventItem]
    ) -> None: ...


class NoSystemEvents:
    """The ingest port of a service built without the ingest module: it records nothing."""

    async def record_coordination_events(self, caller: SourcePrincipal, events: Sequence[EventItem]) -> None:  # noqa: ARG002
        return None


class ContextReader(Protocol):
    """The context compiler's read, which a handoff's package is compiled with (`ServeService.context`)."""

    async def context(self, caller: SourcePrincipal, request: ContextRequest) -> ContextResponse: ...
