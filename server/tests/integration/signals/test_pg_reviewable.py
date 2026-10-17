"""The reviewable profile, review requests and legal holds on PostgreSQL (24.5): inference tombstones read
with the derivation's one statement, the kept tier and the signals' expiry skipping what a hold keeps, and
the requests and holds kept, listed and resolved once."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.privacy.holds import LegalHoldRecord
from niadra.domain.privacy.reviews import ReviewRequestRecord
from niadra.domain.signals.records import SELF, PendingRow, SignalRow, SignalWrite, SuppressionRow
from niadra.domain.turns.records import KeptTurn
from niadra.domain.turns.storage import KeptBy
from niadra.testing.cell import WHATSAPP_SOURCE
from tests.integration.conftest import CloningUnitOfWork

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _kept(turn_id: str, conversation: bytes) -> KeptTurn:
    return KeptTurn(
        turn_id=turn_id, conversation=conversation, source_id=WHATSAPP_SOURCE, agent="closer", kind="message",
        started_at=NOW, fidelity="gold", completeness="complete", content_mode="stored", replayable=True,
        build_hash=None, flags=(), kept_by=KeptBy.REQUEST, kept_at=NOW, kept_until=NOW + timedelta(days=1),
    )  # fmt: skip


async def test_the_kept_tier_expiry_skips_a_held_conversation(pg_uow: CloningUnitOfWork) -> None:
    space, held, free = uuid4(), b"h" * 32, b"f" * 32
    async with pg_uow(space) as uow:
        await uow.turns.keep([_kept("k-held", held), _kept("k-free", free)])
    async with pg_uow(space) as uow:
        expired = await uow.turns.expire_kept(NOW + timedelta(days=2), 10, keep=frozenset({held}))
    assert [k.turn_id for k in expired] == ["k-free"]
    async with pg_uow(space) as uow:
        assert [k.turn_id for k in await uow.turns.kept(["k-held", "k-free"])] == ["k-held"]
        assert [k.turn_id for k in await uow.turns.expire_kept(NOW + timedelta(days=2), 10)] == ["k-held"]


async def test_tombstones_ride_the_derivations_read_and_a_held_handle_keeps_its_signals(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, handle, other = uuid4(), uuid4(), uuid4()
    tombstone = SuppressionRow(handle, SELF, b"k" * 32, "soft_constraint", NOW, NOW)
    async with pg_uow(space) as uow:
        await uow.signals.add_pending([PendingRow("t:1:0", handle, NOW, b"sealed", 1, "s1")])
        await uow.signals.add_suppressions([tombstone])
        later = SuppressionRow(handle, SELF, b"k" * 32, "soft_constraint", NOW + timedelta(hours=1), NOW)
        await uow.signals.add_suppressions([later])
    async with pg_uow(space) as uow:
        due = await uow.signals.due_snapshots(
            NOW + timedelta(minutes=6), window=timedelta(minutes=5), quiet=timedelta(minutes=1), limit=10
        )
        stored = await uow.signals.suppressions_for([handle])
    assert due.snapshots[handle].suppressed == (later,)
    assert stored == [later]
    old = NOW - timedelta(days=400)
    rows = [SignalRow(h, SELF, b"sealed", 1, 1, 6, old, old) for h in (handle, other)]
    async with pg_uow(space) as uow:
        for row in rows:
            await uow.signals.store(SignalWrite(row.handle_id, old, (row,), (), ()))
    async with pg_uow(space) as uow:
        due = await uow.signals.inactive(NOW - timedelta(days=395), limit=10, keep={handle})
    assert [r.handle_id for r in due] == [other]
    scope = ErasureScope(uuid7(), TargetKind.HANDLE, (handle,), None, None, NOW)
    async with pg_uow(space) as uow:
        erased = await uow.signals.erase_for(scope, limit=100)
    assert "inference_suppressions" in {r.table for r in erased}


async def test_a_hold_is_kept_once_released_once_and_found_by_what_it_holds(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, handle = uuid4(), uuid4()
    by_handle = LegalHoldRecord(uuid7(), "handle", NOW, "user:dpo", b"sealed", 1, handle_id=handle)
    by_conversation = LegalHoldRecord(
        uuid7(), "conversation", NOW, "user:dpo", b"sealed", 1, conversation_hmac=b"c" * 32
    )
    async with pg_uow(space) as uow:
        await uow.legal_holds.add(by_handle)
        await uow.legal_holds.add(by_conversation)
        assert await uow.legal_holds.add(by_handle) == by_handle
    async with pg_uow(space) as uow:
        assert [h.hold_id for h in await uow.legal_holds.active()] == sorted(
            [by_handle.hold_id, by_conversation.hold_id]
        )
        released = await uow.legal_holds.release(by_handle.hold_id, at=NOW, by="user:dpo", reason_ct=b"x")
        again = await uow.legal_holds.release(by_handle.hold_id, at=NOW, by="user:dpo", reason_ct=b"x")
        found = await uow.legal_holds.holds_on([handle], [b"c" * 32])
        active = await uow.legal_holds.active()
    assert released is not None
    assert released.status == "released"
    assert again is None
    assert {h.hold_id for h in found} == {by_handle.hold_id, by_conversation.hold_id}
    assert [h.hold_id for h in active] == [by_conversation.hold_id]


async def test_a_review_request_is_listed_newest_first_resolved_once_and_purged_after_its_days(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, profile = uuid4(), uuid4()
    first = ReviewRequestRecord(uuid7(), profile, b"sealed", 1, NOW, explanation={"receipts": []})
    second = ReviewRequestRecord(uuid7(), profile, b"sealed", 1, NOW + timedelta(minutes=1))
    async with pg_uow(space) as uow:
        await uow.review_requests.add(first)
        await uow.review_requests.add(second)
        assert await uow.review_requests.add(first) == first
    async with pg_uow(space) as uow:
        newest = await uow.review_requests.page(status=None, before=None, limit=1)
        rest = await uow.review_requests.page(
            status=None, before=(newest[0].created_at, newest[0].request_id), limit=10
        )
        done = await uow.review_requests.resolve(
            first.request_id, outcome="upheld", resolution_ct=None, at=NOW, by="user:dpo"
        )
        twice = await uow.review_requests.resolve(
            first.request_id, outcome="reversed", resolution_ct=None, at=NOW, by="user:dpo"
        )
        mine = await uow.review_requests.for_profiles([profile])
    assert [r.request_id for r in newest + rest] == [second.request_id, first.request_id]
    assert done is not None
    assert (done.status, done.outcome) == ("resolved", "upheld")
    assert twice is None
    assert len(mine) == 2
    async with pg_uow(space) as uow:
        assert await uow.review_requests.purge_resolved(NOW, limit=10) == 0
        assert await uow.review_requests.purge_resolved(NOW + timedelta(days=1), limit=10) == 1
        assert [r.request_id for r in await uow.review_requests.for_profiles([profile])] == [
            second.request_id
        ]
