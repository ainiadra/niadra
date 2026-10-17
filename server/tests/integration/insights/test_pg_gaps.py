"""Knowledge gaps in PostgreSQL (13, 6.5): a day's `slots` receipts in id order, gap rows that replace their
day and keep their sketch, and the team's reviews."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.audit.chain import append, empty_head
from niadra.domain.common.ids import uuid7
from niadra.domain.insights.gaps import GapDay, GapKey, GapKind
from niadra.domain.vocabulary import ReceiptSurface
from tests.integration.audit.test_receipts_pg import AT, DAY, draft


async def test_slot_receipts_of_a_day_in_id_order(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    slots = [draft(space, n, surface=ReceiptSurface.SLOTS, detail={"why": "{}"}) for n in (1, 2, 3)]
    other = draft(space, 4)
    linked, _ = append(empty_head(space, 0, DAY), [*slots, other])
    async with pg_uow(space) as uow:
        await uow.audit.append(space, linked)
    async with pg_uow(space) as uow:
        first = await uow.audit.slot_receipts(space, DAY, after=None, limit=2)
        rest = await uow.audit.slot_receipts(space, DAY, after=first[-1].draft.receipt_id, limit=2)
        none = await uow.audit.slot_receipts(space, DAY + timedelta(days=1), after=None, limit=2)
    assert [r.draft.receipt_id for r in first + rest] == [s.receipt_id for s in slots]
    assert rest[0].draft.detail == {"why": "{}"}
    assert none == []


async def test_gap_rows_replace_their_day_and_keep_the_sketch(pg_uow: PgUnitOfWorkFactory) -> None:
    space, day = uuid4(), date(2026, 9, 21)
    row = GapDay(day, GapKey(GapKind.NO_RECORD, "refund", "billing"), 3)
    for _ in range(3):
        row.customers.add(space.bytes, uuid7())
    old = GapDay(day - timedelta(days=500), GapKey(GapKind.ANSWERED, "invoice"), 1)
    async with pg_uow(space) as uow:
        assert await uow.insights.gaps_day() is None
        await uow.insights.write_gap_days(day, [GapDay(day, GapKey(GapKind.NO_RECORD, "x"), 9)])
        await uow.insights.write_gap_days(day, [row])
        await uow.insights.write_gap_days(old.day, [old])
        await uow.insights.set_gaps_day(day.toordinal())
    async with pg_uow(space) as uow:
        [stored] = await uow.insights.gap_days(day, day + timedelta(days=1))
        assert await uow.insights.gaps_day() == day.toordinal()
        assert await uow.insights.purge_gap_days(day - timedelta(days=400)) == 1
        assert await uow.insights.gap_days(old.day, day) == []
    assert (stored.key, stored.turns, stored.customers.count()) == (row.key, 3, 3)


async def test_reviews_keep_the_last_word(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    at = datetime(2026, 9, 26, 12, tzinfo=UTC)
    async with pg_uow(space) as uow:
        await uow.insights.put_review("no_record:refund:billing", "dismissed", "person:a", at, None)
        await uow.insights.put_review(
            "no_record:refund:billing", "accepted", "person:b", AT, "connect the ERP"
        )
    async with pg_uow(space) as uow:
        assert await uow.insights.reviews() == {
            "no_record:refund:billing": ("accepted", "person:b", AT, "connect the ERP")
        }
