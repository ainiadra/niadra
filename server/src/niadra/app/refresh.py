"""Named reasons to fetch an object again, with a budget (24.3.5; object-state 8).

A type names its reasons (`never_observed`, `new_activity`, `claim_pending`...), each a condition with a
priority and a budget in the company's units, never in money. They are evaluated when an object is read and
when it is written; the one with the highest priority that holds is the object's `refetch`. Only what the
budget admits becomes a request for the company's resolver worker: a limiter per object and reason in the
state store, the type's minimum interval between fetches, and the reservation of the coordination's paid
budgets (a cost not known reserves the highest measured, and a paid call that fails still counts). A read
never waits for a source, and never runs a statement here: the admitted requests queue in the state store and
a per-space loop writes them in one statement.

`claim_pending` is for the SDK's `verify`, which fetches on the spot: it is admitted and reserved like the
others, and never queued for the worker.

The worker answers a request by pushing the object with the request's id: the answer waits in the state
store, and the consolidator settles it by id in one statement. One it cannot answer it releases. A
`watch_revalidation` (a watch whose condition held on a value that did not come live) is decided on the
answer's values; released, given up, or with no worker to ask, it falls back to the type's
`unconfirmed_watch`.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

import orjson

from niadra.app.typed_state import (
    DEFAULT_PERIOD_S,
    WATCH_REVALIDATION,
    TypedObject,
    Wanted,
    evaluated,
    newest,
    wanted,
)
from niadra.config.object_types import ObjectType
from niadra.contracts.common import ObjectRef
from niadra.contracts.state import Refetch, RefreshReleased, RefreshRequest, RefreshRequestPage, StateRef
from niadra.domain.common.errors import NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.records import BusinessObject
from niadra.domain.state.expr import duration_ms
from niadra.domain.state.shared import SharedKey
from niadra.domain.vocabulary import Feature
from niadra.ports.admission import PaidBudgets, PaidReservation, Unbudgeted
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.repos.typed import RefreshRow
from niadra.ports.state import RefreshWants, WatchRevalidation
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

Priority = Literal["high", "normal", "low"]
CLAIM_PENDING = "claim_pending"
LEASE = timedelta(seconds=60)
"""How long a worker holds the requests it took before they are offered again."""
ATTEMPTS = 3
"""Leases a request gets before it is given up, its paid call counted as failed."""
FLUSH_PAGE = 500
ANSWERS_PAGE = 500
WORKER_SEEN_S = 600
"""A resolver worker that took requests in the last ten minutes is there to ask."""
_CACHE_ERRORS = (ConnectionError, TimeoutError, OSError)


@dataclass(frozen=True, slots=True)
class Ask:
    object_id: UUID
    key: SharedKey
    declared: ObjectType
    wanted: Wanted


class RefreshService:
    def __init__(
        self, uow: UnitOfWorkFactory, wants: RefreshWants, clock: Clock, config: ConfigProvider
    ) -> None:
        self._uow = uow
        self._wants = wants
        self._clock = clock
        self._config = config
        # A paid fetch is admitted against its budget before the resolver worker gets it; the cell wires the
        # coordination's (24.2.12, 24.3.4).
        self.admission: PaidBudgets = Unbudgeted()
        # Where a watch's revalidation that no fresh value answered goes; the cell wires the objects module's.
        self.watches: WatchRevalidation | None = None

    async def want(self, space_id: UUID, asks: Sequence[Ask]) -> list[Refetch]:
        """Whether each reason's budget admits a request now: no statement, and never an error. A request of
        the same object and reason inside its budget's period gets the answer the first one got."""
        answers: list[Refetch] = []
        queued: list[bytes] = []
        pending: dict[str, bytes] = {}
        for ask in asks:
            admitted = await self._admit(space_id, ask, queued, pending)
            answers.append(
                Refetch.model_validate(
                    {"reason": ask.wanted.reason, "priority": ask.wanted.priority, "admitted": admitted}
                )
            )
        if queued or pending:
            ttl = max(DEFAULT_PERIOD_S, int(LEASE.total_seconds()) * ATTEMPTS * 2)
            try:
                await self._wants.enqueue(space_id, queued, pending, ttl)
            except _CACHE_ERRORS:
                log.warning("refresh requests not queued; the next read or write asks again")
        return answers

    async def _admit(self, space_id: UUID, ask: Ask, queued: list[bytes], pending: dict[str, bytes]) -> bool:
        wanted_ = ask.wanted
        if wanted_.never:
            return False
        limiter = f"{ask.object_id}:{wanted_.reason}"
        try:
            earlier = await self._wants.claim(space_id, limiter, wanted_.period_s)
            if earlier is not None:
                return earlier == "admitted"
            budget = ask.declared.refetch.budget if ask.declared.refetch else None
            interval = duration_ms(budget.min_interval) // 1000 if budget and budget.min_interval else 0
            if interval and await self._wants.claim(space_id, str(ask.object_id), interval) is not None:
                await self._wants.settle_claim(space_id, limiter, "denied", interval)
                return False
            reservation = None
            if budget is not None and not wanted_.unlimited:
                ref = ObjectRef(type=ask.key.type, namespace=ask.key.namespace, id=ask.key.external_id)
                reservation = await self.admission.reserve(
                    space_id, budget.unit, object=ref, units=wanted_.units
                )
                if not reservation.admitted:
                    retry = 60 if reservation.reason == "unavailable" else wanted_.period_s
                    await self._wants.settle_claim(space_id, limiter, "denied", min(retry, wanted_.period_s))
                    return False
            await self._wants.settle_claim(space_id, limiter, "admitted", wanted_.period_s)
        except _CACHE_ERRORS:
            return False  # the state store is out: nothing is admitted (L23), and the read goes on
        held = _reservation_json(reservation) if reservation and reservation.reservation_id else None
        if held is not None:
            pending[str(ask.object_id)] = orjson.dumps(held)
        if wanted_.reason != CLAIM_PENDING:
            queued.append(
                orjson.dumps(
                    {
                        "object_id": str(ask.object_id),
                        "key": [ask.key.type, ask.key.namespace, ask.key.external_id, ask.key.variant],
                        "reason": wanted_.reason,
                        "priority": wanted_.priority,
                        "units": wanted_.units or (reservation.units if reservation else 0),
                        "at": self._clock.now().isoformat(),
                        "reservation": held,
                    }
                )
            )
        return True

    async def on_written(self, space_id: UUID, objects: Sequence[BusinessObject]) -> None:
        """After a write: the requests of the objects that came back fresh leave, and the reasons of what was
        written are evaluated as a `display` read would."""
        if not objects:
            return
        await self.refreshed(space_id, [o.object_id for o in objects])
        try:
            space = self._config.space(space_id)
        except NotFound:
            return
        if Feature.STATE not in space.features.enabled:
            return
        names = {source.source_id: source.name for source in space.sources}
        now = self._clock.now()
        asks = []
        for obj in objects:
            declared = space.object_types.get(obj.key.type)
            if declared is None or declared.refetch is None:
                continue
            typed = TypedObject(obj.key, obj.fields, obj.as_of, obj.derived_status, obj.typed)
            seen = evaluated(typed, declared, names, now)
            found = wanted(declared, seen.env, newest(obj.fields), now)
            if found is not None:
                key = SharedKey(obj.key.type, obj.key.namespace, obj.key.external_id)
                asks.append(Ask(obj.object_id, key, declared, found))
        await self.want(space_id, asks)

    async def flush(self, space_id: UUID) -> int:
        """A per-space loop step: the admitted requests, in one statement."""
        try:
            taken = await self._wants.take(space_id, FLUSH_PAGE)
        except _CACHE_ERRORS:
            return 0
        if not taken:
            return 0
        rows = []
        for raw in taken:
            body = orjson.loads(raw)
            rows.append(
                RefreshRow(
                    uuid7(),
                    UUID(body["object_id"]),
                    SharedKey(*body["key"]),
                    body["reason"],
                    body["priority"],
                    int(body["units"]),
                    datetime.fromisoformat(body["at"]),
                    body["reservation"],
                )
            )
        try:
            async with self._uow(space_id) as uow:
                await uow.typed.request_refreshes(rows)
        except Exception:
            await self._wants.requeue(space_id, taken)
            raise
        return len(rows)

    async def lease(self, caller: SourcePrincipal, limit: int) -> RefreshRequestPage:
        """`GET /v1/state/refresh-requests` (24.3.12): the worker's next requests, leased for 60 s, in one
        statement."""
        now = self._clock.now()
        owner = str(caller.key_id or caller.source_id)
        async with self._uow(caller.space.space_id) as uow:
            rows = await uow.typed.lease_refreshes(owner, now, now + LEASE, limit=limit)
        try:
            await self._wants.worker_seen(caller.space.space_id, WORKER_SEEN_S)
        except _CACHE_ERRORS:
            log.warning("resolver worker not marked; watches fall back until the next lease marks it")
        return RefreshRequestPage(
            items=[
                RefreshRequest(
                    request_id=str(row.request_id),
                    ref=StateRef(
                        type=row.key.type,
                        namespace=row.key.namespace,
                        id=row.key.external_id,
                        variant=row.key.variant or None,
                    ),
                    reason=row.reason,
                    priority=cast(Priority, row.priority),
                    budget_units=row.budget_units,
                    lease_until=row.lease_until or now + LEASE,
                )
                for row in rows
            ]
        )

    async def refreshed(self, space_id: UUID, object_ids: Iterable[UUID]) -> None:
        """After a write: the requests of the objects that came back fresh leave, and their reservations are
        settled as done. No statement unless one of them had a request outstanding."""
        ids = [str(i) for i in dict.fromkeys(object_ids)]
        try:
            outstanding = await self._wants.outstanding(space_id, ids)
        except _CACHE_ERRORS:
            return
        if not outstanding:
            return
        async with self._uow(space_id) as uow:
            await uow.typed.refreshed([UUID(i) for i in outstanding])
        for raw in outstanding.values():
            await self.admission.settle(space_id, _reservation_of(orjson.loads(raw)), outcome="done")

    async def give_up(self, space_id: UUID) -> int:
        """A per-space loop step: requests leased three times and never answered leave; a paid call that
        failed still counts."""
        async with self._uow(space_id) as uow:
            given = await uow.typed.give_up_refreshes(self._clock.now(), ATTEMPTS, limit=FLUSH_PAGE)
        await self._unanswered(space_id, given)
        return len(given)

    async def release(self, caller: SourcePrincipal, request_id: str, outcome: str) -> RefreshReleased:
        """`POST /v1/state/refresh-requests/{request_id}/release`: a request the worker cannot answer leaves
        now, in one statement, instead of waiting for its leases to run out."""
        try:
            wanted_id = UUID(request_id)
        except ValueError:
            raise NotFound("no such refresh request") from None
        space_id = caller.space.space_id
        async with self._uow(space_id) as uow:
            rows = await uow.typed.settle_refreshes([wanted_id])
        if not rows:
            raise NotFound("no such refresh request")
        log.info("refresh request released", extra={"reason": rows[0].reason, "outcome": outcome})
        await self._unanswered(space_id, rows)
        return RefreshReleased(request_id=request_id, reason=rows[0].reason)

    async def answered(self, space_id: UUID, answers: Sequence[Mapping[str, Any]]) -> None:
        """A push's items that name a request: kept in the state store for the consolidator, never a
        statement on the push. Lost with the store, the request is given up after its leases."""
        if not answers:
            return
        try:
            await self._wants.answered(
                space_id, [orjson.dumps(a) for a in answers], int(LEASE.total_seconds()) * 10
            )
        except _CACHE_ERRORS:
            log.warning("refresh answers not kept; their requests are given up after their leases")

    async def settle_answered(self, space_id: UUID) -> list[tuple[RefreshRow, dict[str, Any]]]:
        """A consolidator step: the requests the pushes answered leave, by id, in one statement, their paid
        calls settled as done; each comes back with what its push brought."""
        try:
            taken = await self._wants.take_answered(space_id, ANSWERS_PAGE)
        except _CACHE_ERRORS:
            return []
        if not taken:
            return []
        bodies: dict[UUID, dict[str, Any]] = {}
        for raw in taken:
            body = orjson.loads(raw)
            try:
                bodies[UUID(body["request_id"])] = body
            except ValueError:
                continue  # not an id this cell leased
        try:
            async with self._uow(space_id) as uow:
                rows = await uow.typed.settle_refreshes(list(bodies))
        except Exception:
            await self._wants.requeue_answered(space_id, taken)
            raise
        for row in rows:
            if row.reservation:
                await self.admission.settle(space_id, _reservation_of(row.reservation), outcome="done")
        return [(row, bodies[row.request_id]) for row in rows]

    async def has_worker(self, space_id: UUID) -> bool:
        try:
            return await self._wants.has_worker(space_id)
        except _CACHE_ERRORS:
            return False

    async def _unanswered(self, space_id: UUID, rows: Sequence[RefreshRow]) -> None:
        for row in rows:
            if row.reservation:
                await self.admission.settle(space_id, _reservation_of(row.reservation), outcome="failed")
        watched = [row.object_id for row in rows if row.reason == WATCH_REVALIDATION]
        if watched and self.watches is not None:
            await self.watches.unconfirmed(space_id, watched)


def _reservation_json(reservation: PaidReservation) -> dict[str, Any]:
    body = asdict(reservation)
    body["retry_at"] = reservation.retry_at.isoformat() if reservation.retry_at else None
    body["reservation_id"] = str(reservation.reservation_id) if reservation.reservation_id else None
    return body


def _reservation_of(body: Mapping[str, Any]) -> PaidReservation:
    return PaidReservation(
        admitted=bool(body["admitted"]),
        operation=str(body["operation"]),
        units=int(body["units"]),
        reason=body.get("reason"),
        remaining=body.get("remaining"),
        retry_at=datetime.fromisoformat(body["retry_at"]) if body.get("retry_at") else None,
        reservation_id=UUID(body["reservation_id"]) if body.get("reservation_id") else None,
        target=body.get("target"),
    )
