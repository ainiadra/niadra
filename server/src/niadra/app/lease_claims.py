"""Who takes a busy profile lease next: a task that must not wait behind a burst of the profile's work.

A leased task that finds its profile's lease busy is delayed by `BUSY_DELAY_S` and tried again, so when the
lease frees, whichever task asks first takes it. A burst of one customer's work (a re-extraction pass of
their sessions, an imported history: dozens of `reconcile`, `embed` and `compile` tasks) then turns the
lease into a lottery that a business object's event can lose for minutes, while an agent waits to read
the invoice it names. Release 2026.09.28-1750-da68f12 failed its end-to-end check that way:
`apply_object_event` lost the lease about fifty times in two minutes to the re-extraction pass of the
e2e customer's sessions.

So an urgent task (`TaskSpec.urgent`) that finds the lease busy leaves a claim on it in the state store
and asks again, first after `POLL_S` and then less and less often, for up to `WAIT_S`, while any other
task that finds a claim not its own treats the lease as busy. The lease frees, and the next to take it is
the urgent task. The claim is advisory: the lease row still decides, and a claim expires by itself
(`CLAIM_S`) if its task dies. A failing store only means no claims, so the lease goes to whoever asks
first, never a task that cannot run.
"""

import logging
from collections.abc import Sequence
from uuid import UUID

from niadra.ports.infra import KeyValueCache

log = logging.getLogger(__name__)

POLL_S = 0.1
"""How soon an urgent task first asks again for the lease it claimed."""
POLL_MAX_S = 1.0
"""Each next ask waits twice as long, up to this: a holder that runs for seconds costs a space database a
few statements, not one every `POLL_S` (the workers share one connection per space database)."""
WAIT_S = 30.0
"""How long an urgent task waits for a busy lease before it is delayed like any other; under the lease's
time to live, so a holder that never releases does not keep it waiting past what the lease allows."""
CLAIM_S = 5
"""A claim's time to live, renewed on every ask: a task that died leaves the lease to the others soon."""


def claim_key(space_id: UUID, unit: str) -> str:
    return f"lc:{space_id}:{unit}"


class LeaseClaims:
    def __init__(self, store: KeyValueCache) -> None:
        self._store = store

    async def claim(self, space_id: UUID, units: Sequence[str], owner: str) -> None:
        try:
            for unit in units:
                await self._store.set(claim_key(space_id, unit), owner.encode(), CLAIM_S)
        except Exception:
            log.warning("claiming a busy lease failed; the task waits in turn", exc_info=True)

    async def claimed_by_another(self, space_id: UUID, units: Sequence[str], owner: str) -> bool:
        """Whether an urgent task other than `owner` waits for any of these units."""
        if not units:
            return False
        try:
            found = await self._store.get_many([claim_key(space_id, unit) for unit in units])
        except Exception:
            log.warning("reading lease claims failed; the lease goes to whoever asks", exc_info=True)
            return False
        return any(value is not None and value != owner.encode() for value in found)

    async def drop(self, space_id: UUID, units: Sequence[str], owner: str) -> None:
        """Only the claim this owner left: another urgent task may have claimed the unit since."""
        try:
            keys = [claim_key(space_id, unit) for unit in units]
            found = await self._store.get_many(keys)
            mine = [k for k, value in zip(keys, found, strict=True) if value == owner.encode()]
            if mine:
                await self._store.delete(mine)
        except Exception:
            log.warning("dropping a lease claim failed; it expires by itself", exc_info=True)
