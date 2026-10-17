from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from niadra.adapters.memory.fakes import FixedClock, InMemoryObjectStore
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.audit import AuditService
from niadra.domain.audit.chain import append, empty_head
from niadra.domain.audit.watermarks import ProducerState, ProducerStatus
from niadra.domain.common.ids import uuid7
from niadra.domain.receipts import PERSON_LABEL, ReceiptDraft
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface
from niadra.ports.repos.audit import AuditRoot, ReceiptQuery

AT = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
DAY = date(2026, 9, 22)


def draft(space: UUID, seq: int, producer: str = "read-pg", **fields: Any) -> ReceiptDraft:
    read_at = AT + timedelta(milliseconds=seq)
    values: dict[str, Any] = {
        "receipt_id": uuid7(int(read_at.timestamp() * 1000)),
        "space_id": space,
        "producer_id": producer,
        "producer_seq": seq,
        "kind": ReceiptKind.READ,
        "surface": ReceiptSurface.PACK,
        "audience": Audience.CUSTOMER_AGENT,
        "read_at": read_at,
        "source_id": uuid4(),
        "item_ids": ("fact-1",),
        "query_hash": b"\x01\x02",
        "verification": "V1",
    }
    values.update(fields)
    return ReceiptDraft(**values)


class NoSource:
    async def batches(self, max_items: int):  # type: ignore[no-untyped-def]
        return
        yield


def service(pg_uow: PgUnitOfWorkFactory) -> AuditService:
    return AuditService(
        pg_uow, NoSource(), InMemoryObjectStore(), FixedClock(AT + timedelta(seconds=5)), slices={}
    )


async def test_the_writer_stores_a_valid_chain_and_ignores_redelivery(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    audit = service(pg_uow)
    first = [draft(space, i, producer=f"p-{space}") for i in range(1, 4)]
    assert await audit.write(first) == 3
    assert await audit.write([first[0], draft(space, 4, producer=f"p-{space}")]) == 1
    result = await audit.verify_chain(space, DAY)
    assert result.valid
    assert result.receipts == 4
    async with pg_uow(space) as uow:
        stored = await uow.audit.get_receipt(space, first[1].receipt_id)
    assert stored is not None
    assert stored.draft == first[1]


async def test_tampering_in_the_database_is_detected(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    audit = service(pg_uow)
    drafts = [draft(space, i, producer=f"p-{space}") for i in range(1, 4)]
    await audit.write(drafts)
    async with pg_uow(space) as uow:
        await uow.session.execute(
            text("UPDATE receipts SET purpose = 'sales' WHERE space_id = :s AND receipt_id = :r"),
            {"s": space, "r": drafts[1].receipt_id},
        )
    result = await audit.verify_chain(space, DAY)
    assert [(b.seq, b.reason) for b in result.breaks] == [(2, "content_mismatch")]


async def test_receipts_land_in_their_weekly_partition(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    # A week no other test writes to: the default partition must hold none of its rows.
    at = AT + timedelta(weeks=100)
    week_ms = 7 * 24 * 3600 * 1000
    start = int(at.timestamp() * 1000) // week_ms * week_ms
    # As the maintenance role: the partition function runs with its owner's rights.
    async with pg_uow.system(space) as uow:
        await uow.session.execute(text("SELECT ensure_week_partition('receipts', :w)"), {"w": start})
    receipt = draft(space, 1, read_at=at, receipt_id=uuid7(int(at.timestamp() * 1000)))
    linked, _ = append(empty_head(space, 0, at.date()), [receipt])
    async with pg_uow(space) as uow:
        await uow.audit.append(space, linked)
        partition = (
            await uow.session.execute(
                text("SELECT tableoid::regclass::text FROM receipts WHERE receipt_id = :r"),
                {"r": receipt.receipt_id},
            )
        ).scalar_one()
    assert partition == f"receipts_w{start // week_ms}"


async def test_queries_filter_page_and_find_items(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile = uuid4(), uuid4()
    audit = service(pg_uow)
    mine = [
        draft(space, i, profile_id=profile, conversation_id="c-1", item_ids=(f"f-{i}", "shared"))
        for i in range(1, 6)
    ]
    other = draft(space, 6, kind=ReceiptKind.HISTORY, surface=ReceiptSurface.SEARCH)
    await audit.write([*mine, other])
    async with pg_uow(space) as uow:
        page = await uow.audit.list_receipts(space, ReceiptQuery(profile_id=profile, limit=3))
        rest = await uow.audit.list_receipts(
            space, ReceiptQuery(profile_id=profile, limit=3, before_id=page[-1].draft.receipt_id)
        )
        history = await uow.audit.list_receipts(
            space, ReceiptQuery(kind=ReceiptKind.HISTORY, since=AT, until=AT + timedelta(1))
        )
        seen = await uow.audit.receipts_with_item(space, "f-2", profile_id=profile, limit=10)
        shared = await uow.audit.receipts_with_item(space, "shared", profile_id=None, limit=10)
        by_conversation = await uow.audit.receipts_for_conversation("c-1", since=AT)
        exported = await uow.audit.export_rows("receipts", after_seq=0, upto_seq=2**62, limit=100)
    assert [r.draft.receipt_id for r in page + rest] == [d.receipt_id for d in reversed(mine)]
    assert [r.draft.receipt_id for r in history] == [other.receipt_id]
    assert [r.draft.receipt_id for r in seen] == [mine[1].receipt_id]
    assert len(shared) == 5
    assert [r["receipt_id"] for r in by_conversation] == [d.receipt_id for d in mine]
    assert {r["receipt_id"] for r in exported} == {d.receipt_id for d in [*mine, other]}
    assert all(r["change_seq"] > 0 for r in exported)


async def test_producer_states_round_trip_and_make_the_watermark(pg_uow: PgUnitOfWorkFactory) -> None:
    async with pg_uow.cell() as uow:
        await uow.session.execute(text("DELETE FROM receipt_watermarks"))
        states = [
            ProducerState("slow", 4, AT, ProducerStatus.ACTIVE, AT, frozenset({6, 7})),
            ProducerState("done", 9, AT + timedelta(minutes=1), ProducerStatus.CLOSED, AT),
        ]
        await uow.watermarks.save_producers(states)
        await uow.watermarks.save_producers(
            [ProducerState("slow", 7, AT + timedelta(seconds=3), ProducerStatus.ACTIVE, AT)]
        )
    async with pg_uow.cell() as uow:
        loaded = {s.producer_id: s for s in await uow.watermarks.load_producers()}
        assert loaded["slow"].contiguous_seq == 7
        assert loaded["slow"].pending_seqs == frozenset()
        assert loaded["done"] == states[1]
        assert await uow.watermarks.watermark() == AT + timedelta(seconds=3)
        assert [s.producer_id for s in await uow.watermarks.load_producers(["done"])] == ["done"]


async def test_the_anchor_writes_the_root_once(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    audit = service(pg_uow)
    await audit.write([draft(space, 1, producer=f"p-{space}")])
    async with pg_uow.cell() as uow:
        await uow.session.execute(text("DELETE FROM receipt_watermarks"))
    await audit.audit_anchor(Task(TaskName.AUDIT_ANCHOR, space, {"day": DAY.isoformat()}))
    result = await audit.verify_chain(space, DAY)
    assert result.root_matches is True
    async with pg_uow(space) as uow:
        assert not await uow.audit.save_root(space, AuditRoot(DAY, b"x", "elsewhere", AT))
        root = await uow.audit.get_root(space, DAY)
    assert root is not None
    assert root.location == f"audit/{space}/{DAY.isoformat()}.json"


async def test_row_level_security_hides_other_spaces(
    pg_uow: PgUnitOfWorkFactory, rls_uow: PgUnitOfWorkFactory
) -> None:
    mine, theirs = uuid4(), uuid4()
    for space in (mine, theirs):
        linked, head = append(empty_head(space, 0, DAY), [draft(space, 1)])
        async with rls_uow(space) as uow:
            await uow.audit.lock_head(space, 0, DAY)
            await uow.audit.append(space, linked)
            await uow.audit.save_head(space, head)
    async with rls_uow(mine) as uow:
        visible = await uow.session.execute(text("SELECT DISTINCT space_id FROM receipts"))
        heads = await uow.session.execute(text("SELECT DISTINCT space_id FROM receipt_heads"))
        assert set(visible.scalars()) == {mine}
        assert set(heads.scalars()) == {mine}
        assert await uow.audit.list_receipts(theirs, ReceiptQuery()) == []


async def test_a_refusal_keeps_its_reason_on_the_chain(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    audit = service(pg_uow)
    refused = replace(
        draft(space, 1, producer=f"p-{space}"), kind=ReceiptKind.DENIED, detail={"reason": "forbidden"}
    )
    await audit.write([refused, draft(space, 2, producer=f"p-{space}")])
    page = await audit.list_receipts(space, ReceiptQuery(kind=ReceiptKind.DENIED))
    assert [(r.kind, r.detail) for r in page.items] == [(ReceiptKind.DENIED, {"reason": "forbidden"})]
    assert (await audit.verify_chain(space, DAY)).valid
    async with pg_uow(space) as uow:
        await uow.session.execute(
            text(
                'UPDATE receipts SET detail = \'{"reason": "none"}\' WHERE space_id = :s AND receipt_id = :r'
            ),
            {"s": space, "r": refused.receipt_id},
        )
    assert [b.reason for b in (await audit.verify_chain(space, DAY)).breaks] == ["content_mismatch"]


async def test_the_writer_keeps_the_name_of_the_person_who_acted_beside_the_chain(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space, other, ana, unnamed = uuid4(), uuid4(), uuid4(), uuid4()
    audit = service(pg_uow)
    person = {"kind": ReceiptKind.ADMIN, "surface": ReceiptSurface.CONSOLE, "source_id": None}
    old = draft(space, 1, f"p-{space}", person_id=ana, extra={PERSON_LABEL: "ana@old.com"}, **person)
    new = draft(space, 2, f"p-{space}", person_id=ana, extra={PERSON_LABEL: "ana@acme.com"}, **person)
    await audit.write([new, draft(space, 3, f"p-{space}", person_id=unnamed, **person)])
    await audit.write([old])  # an earlier action delivered late keeps the newer name
    await audit.write([draft(space, 4, f"p-{space}", person_id=ana, extra={PERSON_LABEL: "ana@acme.com"})])

    page = await audit.list_receipts(space, ReceiptQuery())

    assert {(r.person_id, r.person_label) for r in page.items} == {
        (str(ana), "ana@acme.com"),
        (str(unnamed), None),
    }
    async with pg_uow(space) as uow:
        assert await uow.audit.person_labels([ana, unnamed]) == {ana: "ana@acme.com"}
    # Another space of the same cell never reads it.
    async with pg_uow(other) as uow:
        assert await uow.audit.person_labels([ana]) == {}
