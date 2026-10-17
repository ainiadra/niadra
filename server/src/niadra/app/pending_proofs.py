"""What a conversation's acknowledged turns prove while they wait in the intake log (5.2, 2.2).

A batch of conversation turns is answered once its intake row is written (`IngestService._accept`), and
the proof the channel gives the conversation (an inbound customer turn's plausibility, a call's network
attestation) reaches its session only when a worker stores the row. An agent reads `context()` for the
conversation right after sending its first turn: without this, that read finds no proof and falls to V0
until the worker has stored the turn, so the agent is told to ask a customer the channel already
identified to verify.

So the acknowledgement holds each stored turn's proof in the state store, under a keyed hash of the
conversation (never its id), and a read that finds no proof in the database counts it
(`ReadSupport.verification`). Storing the turn releases it, and it expires after `HOLD_MS` in any case.

Only what storing records is held: the same levels `IngestService._store_event` gives the session, within
the source's ceiling, and a hold never lowers what the database proves. Losing it (a store restart, a
failed write) only sends the read back to what the database proves: V0 until the turn is stored, never
more than was proven.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from niadra.app.intake import PreparedEvent
from niadra.app.read_state import thread_key
from niadra.domain.ingest.rules import highest
from niadra.domain.vocabulary import Verification
from niadra.ports.infra import HotLayer, KeyVault

log = logging.getLogger(__name__)

HOLD_MS = 60_000
"""How long a held proof lasts without its turn being stored: a worker stores a row within a second, and
a row still waiting past this reads V0 until it is stored (the IntakeBacklog alert fires long before)."""
PURPOSE = "pending_proof"


@dataclass(frozen=True, slots=True)
class HeldProof:
    thread: str
    event_id: UUID
    level: Verification


def held_proofs(events: Iterable[PreparedEvent], ceiling: Verification) -> list[HeldProof]:
    """The proofs storing these events records for their conversation or task (`_store_event`)."""
    held = []
    for event in events:
        thread = thread_key(event.conversation_id, event.task_id)
        if thread is None or not (event.network or event.channel_proof):
            continue
        level = highest(event.network, event.channel_proof)
        if Verification.V0.rank < level.rank <= ceiling.rank:
            held.append(HeldProof(thread, event.event_id, level))
    return held


class PendingProofs:
    def __init__(self, keys: KeyVault, hot: HotLayer, *, hold_ms: int = HOLD_MS) -> None:
        self._keys = keys
        self._hot = hot
        self._hold_ms = hold_ms

    async def hold(self, space_id: UUID, held: Sequence[HeldProof]) -> None:
        """Before the acknowledgement returns. A failure is logged: the batch is stored all the same."""
        if not held:
            return
        try:
            items = [(await self._name(space_id, h.thread), h.event_id, h.level) for h in held]
            await self._hot.hold_proofs(items, self._hold_ms)
        except Exception:
            log.warning(
                "holding an acknowledged turn's proof failed; reads wait for it to be stored", exc_info=True
            )

    async def release(self, space_id: UUID, held: Sequence[HeldProof]) -> None:
        """Once the turns are stored and their session holds the proof."""
        if not held:
            return
        try:
            items = [(await self._name(space_id, h.thread), h.event_id) for h in held]
            await self._hot.release_proofs(items)
        except Exception:
            log.warning("releasing a stored turn's proof failed; it expires by itself", exc_info=True)

    async def level(self, space_id: UUID, thread: str) -> Verification | None:
        """The highest level the conversation's turns waiting to be stored prove; None on any failure."""
        try:
            return await self._hot.held_proof(await self._name(space_id, thread))
        except Exception:
            log.warning("reading a held proof failed; the read keeps what the database proves", exc_info=True)
            return None

    async def _name(self, space_id: UUID, thread: str) -> str:
        # A keyed hash: a conversation id can carry a phone number, and the store never holds one.
        digest = await self._keys.hmac(space_id, PURPOSE, thread.encode())
        return f"hp:{space_id}:{{{digest.hex()}}}"
