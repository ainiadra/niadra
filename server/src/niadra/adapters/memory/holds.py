"""In-memory `LegalHoldRepository` for unit tests."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.holds import LegalHoldRecord


class InMemoryLegalHoldRepository:
    def __init__(self, store: MemoryStore) -> None:
        self.holds: dict[UUID, LegalHoldRecord] = store.module("legal_holds").setdefault("holds", {})

    async def add(self, hold: LegalHoldRecord) -> LegalHoldRecord:
        return self.holds.setdefault(hold.hold_id, hold)

    async def get(self, hold_id: UUID) -> LegalHoldRecord | None:
        return self.holds.get(hold_id)

    async def release(
        self, hold_id: UUID, *, at: datetime, by: str, reason_ct: bytes
    ) -> LegalHoldRecord | None:
        hold = self.holds.get(hold_id)
        if hold is None or hold.status != "active":
            return None
        released = replace(
            hold, status="released", released_at=at, released_by=by, release_reason_ct=reason_ct
        )
        self.holds[hold_id] = released
        return released

    async def active(self) -> list[LegalHoldRecord]:
        return sorted((h for h in self.holds.values() if h.status == "active"), key=lambda h: h.hold_id)

    async def holds_on(
        self, handle_ids: Sequence[UUID], conversation_hmacs: Sequence[bytes]
    ) -> list[LegalHoldRecord]:
        handles, conversations = set(handle_ids), set(conversation_hmacs)
        return sorted(
            (
                h
                for h in self.holds.values()
                if h.handle_id in handles or h.conversation_hmac in conversations
            ),
            key=lambda h: h.hold_id,
        )
