"""Receipts of governance actions (kind `admin`, 5.13, 5.22): who did what, on which profile or setting,
with which role. They join the same chain as the reads.

Identifiers only: `target` names a rule, a schedule, a sample or a request by id, never a handle value.
The recorder is its own receipt producer (`<producer_id>/admin`): it keeps its own sequence, and sharing
the read path's producer id would interleave two counters and break the contiguity the watermark relies on.
"""

import itertools
import logging
from dataclasses import replace
from uuid import UUID

from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, Principal, SourcePrincipal
from niadra.domain.receipts import ProducerMark, ReceiptDraft, person_extra
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface
from niadra.ports.infra import Clock, ReceiptSink

log = logging.getLogger(__name__)

EMIT_TIMEOUT_S = 0.5
READ_EMIT_TIMEOUT_S = 0.02
"""What a read waits for its receipt to be confirmed, as the read path waits for its own
(`serve_reads.RECEIPT_TIMEOUT_S`); one not confirmed by then waits in memory and leaves with the next record
or the heartbeat. An agent's block of notes waited up to `EMIT_TIMEOUT_S` for it, and the client gave up at
its 0.6 s whenever the queue was slow to confirm (09/10/2026)."""
MAX_BUFFERED = 10_000


class AdminAudit:
    def __init__(self, receipts: ReceiptSink, clock: Clock, producer_id: str) -> None:
        self._receipts = receipts
        self._clock = clock
        self.producer_id = f"{producer_id}/admin"
        self._seq = itertools.count(1)
        self._last_seq = 0
        self._pending: list[ReceiptDraft] = []

    async def record(
        self,
        principal: Principal,
        *,
        action: str,
        profile_id: UUID | None = None,
        target: str | None = None,
    ) -> None:
        """Called after the use case succeeded. The action already happened, so a broker outage does not
        fail the request: the receipt waits in memory and leaves with the next record or heartbeat."""
        self._last_seq = next(self._seq)
        self._pending.append(self._draft(principal, action, profile_id, target, self._last_seq))
        await self._flush()

    async def record_read(
        self,
        principal: Principal,
        *,
        kind: ReceiptKind,
        surface: ReceiptSurface,
        item_ids: tuple[str, ...],
        conversation_id: str | None = None,
        task_id: str | None = None,
        query_hash: bytes | None = None,
    ) -> None:
        """A read outside the customer's memory (the agents' own notes, 10 C.4), on the same producer and
        chain as the governance actions, and with the same no-fail rule; it waits for its receipt only as
        long as a read does (`READ_EMIT_TIMEOUT_S`)."""
        self._last_seq = next(self._seq)
        draft = self._draft(principal, surface.value, None, None, self._last_seq)
        self._pending.append(
            replace(
                draft,
                kind=kind,
                surface=surface,
                item_ids=item_ids,
                conversation_id=conversation_id,
                task_id=task_id,
                query_hash=query_hash,
                detail={k: v for k, v in draft.detail.items() if k != "action"},
            )
        )
        await self._flush(READ_EMIT_TIMEOUT_S)

    async def record_system(self, space_id: UUID, action: str, target: str) -> None:
        """A change the control plane made (a configuration applied, a key issued or revoked), recorded
        where the space's other governance actions are. No person or key of this cell did it."""
        self._last_seq = next(self._seq)
        self._pending.append(
            ReceiptDraft(
                receipt_id=uuid7(),
                space_id=space_id,
                producer_id=self.producer_id,
                producer_seq=self._last_seq,
                kind=ReceiptKind.ADMIN,
                surface=ReceiptSurface.CONSOLE,
                audience=Audience.HUMAN,
                read_at=self._clock.now(),
                detail={"action": action, "target": target, "actor": "control_plane"},
            )
        )
        await self._flush()

    async def heartbeat(self, *, final: bool = False) -> None:
        """Run on the read path's receipt heartbeat, so an idle recorder never holds the watermark (5.11)."""
        await self._flush()
        if not self._pending:
            mark = ProducerMark(self.producer_id, self._last_seq, self._clock.now(), final)
            await self._receipts.mark(mark)

    def _draft(
        self, principal: Principal, action: str, profile_id: UUID | None, target: str | None, seq: int
    ) -> ReceiptDraft:
        # Stored and chained, so the trail says what was done and by whom, not only that something was.
        detail = {"action": action}
        if target is not None:
            detail["target"] = target
        if isinstance(principal, PersonPrincipal):
            detail["roles"] = ",".join(sorted(principal.roles))
        else:
            detail["key_id"] = principal.key_id
        return ReceiptDraft(
            receipt_id=uuid7(),
            space_id=principal.space.space_id,
            producer_id=self.producer_id,
            producer_seq=seq,
            kind=ReceiptKind.ADMIN,
            surface=ReceiptSurface.CONSOLE,
            audience=principal.audience if isinstance(principal, SourcePrincipal) else Audience.HUMAN,
            read_at=self._clock.now(),
            profile_id=profile_id,
            source_id=principal.source_id if isinstance(principal, SourcePrincipal) else None,
            person_id=principal.user_id if isinstance(principal, PersonPrincipal) else None,
            extra=person_extra(principal),
            detail=detail,
        )

    async def _flush(self, timeout_s: float = EMIT_TIMEOUT_S) -> None:
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        try:
            sent = await self._receipts.emit(batch, timeout_s)
        except Exception:
            log.exception("admin receipts could not be emitted")
            sent = False
        if not sent:
            # The oldest go first when the buffer overflows; the writer then reports the gap as lost (5.11).
            self._pending = (batch + self._pending)[-MAX_BUFFERED:]
