"""A batch of receipts moves the pins and the agent watermarks in one statement each, and leaves them as the
receipts applied one by one would, in any order they arrive (5.11, rev. 11)."""

import random
from datetime import timedelta
from uuid import UUID

from niadra.adapters.memory.serve import InMemoryServeRepository
from niadra.adapters.memory.store import MemoryStore
from niadra.app.audit import _project
from niadra.domain.receipts import ReceiptDraft

from .conftest import BASE, draft

PROFILE = UUID("0192f5a0-0000-7000-8000-00000000f001")


class _Uow:
    def __init__(self) -> None:
        self.serve = InMemoryServeRepository(MemoryStore())
        self.compile = self

    async def mark_read(self, reads: object) -> None:
        return None


def receipts(seed: int) -> list[ReceiptDraft]:
    rnd = random.Random(seed)
    out = []
    for n in range(12):
        conversation = rnd.choice(["c1", "c2", "c3"])
        generation = rnd.randint(1, 4)
        at = BASE + timedelta(seconds=rnd.randint(0, 50))
        out.append(
            draft(
                seq=n + 1,
                at=at,
                profile_id=PROFILE,
                conversation_id=conversation,
                pack_hash=f"h{generation}",
                manifest_hash=f"m{generation}",
                extra={"view": "chat", "pack_key": f"{PROFILE}/chat", "generation": str(generation)},
            )
        )
    return out


async def test_a_batch_leaves_the_pins_and_marks_the_receipts_one_by_one_leave() -> None:
    for seed in range(20):
        drafts = receipts(seed)
        together, one_by_one = _Uow(), _Uow()
        await _project(together, drafts)  # type: ignore[arg-type]
        for d in drafts:
            await _project(one_by_one, [d])  # type: ignore[arg-type]
        assert together.serve._pins == one_by_one.serve._pins
        assert together.serve._marks == one_by_one.serve._marks
