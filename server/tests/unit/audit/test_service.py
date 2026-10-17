import dataclasses
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from niadra.adapters.memory.fakes import FixedClock, InMemoryAnchorStore, InMemoryObjectStore
from niadra.adapters.memory.uow import InMemoryUnitOfWorkFactory
from niadra.app.audit import AuditService
from niadra.domain.audit.chain import canonical, merkle_root
from niadra.domain.common.errors import NotFound
from niadra.domain.receipts import PERSON_LABEL, ProducerMark, ReceiptDraft
from niadra.domain.records import EventRecord, ProfileRecord
from niadra.domain.serve.records import AgentWatermark, ConversationPin
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, ReceiptKind, Speaker, SubjectKind
from niadra.ports.repos.audit import ReceiptQuery
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, PatchedUnitOfWorkFactory

from .conftest import BASE, draft

DAY = date(2026, 9, 22)


class FakeServe:
    def __init__(self, *, down: bool = False) -> None:
        self.down = down
        self.pins: list[dict[str, Any]] = []
        self.watermarks: list[dict[str, Any]] = []

    async def _pin(self, **kw: Any) -> None:
        if self.down:
            raise ConnectionError("database went away")
        self.pins.append(kw)

    async def _watermark(self, **kw: Any) -> None:
        self.watermarks.append(kw)

    async def project_pins(self, pins: Sequence[ConversationPin]) -> None:
        for pin in pins:
            await self._pin(
                conversation_id=pin.conversation_id,
                view=pin.view,
                pack_key=pin.pack_key,
                pack_hash=pin.pack_hash,
                generation=pin.generation,
                pinned_at=pin.pinned_at,
            )

    async def project_watermarks(self, marks: Sequence[AgentWatermark]) -> None:
        for mark in marks:
            await self._watermark(
                source_id=mark.source_id,
                profile_id=mark.profile_id,
                manifest_hash=mark.manifest_hash,
                seen_at=mark.seen_at,
                thread=mark.thread,
                generation=mark.generation,
            )


@dataclass
class FakeBatch:
    drafts: list[ReceiptDraft]
    marks: list[ProducerMark] = field(default_factory=list)
    acked: bool = False
    retried: bool = False

    async def ack(self) -> None:
        self.acked = True

    async def retry_later(self) -> None:
        self.retried = True


class FakeSource:
    def __init__(self, *batches: FakeBatch) -> None:
        self.queue = list(batches)

    async def batches(self, max_items: int) -> AsyncIterator[FakeBatch]:
        while self.queue:
            yield self.queue.pop(0)


@pytest.fixture
def base() -> InMemoryUnitOfWorkFactory:
    return InMemoryUnitOfWorkFactory()


@pytest.fixture
def serve() -> FakeServe:
    return FakeServe()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(BASE + timedelta(seconds=5))


def service(
    base: InMemoryUnitOfWorkFactory,
    clock: FixedClock,
    *,
    source: FakeSource | None = None,
    objects: InMemoryObjectStore | None = None,
    slices: dict[UUID, int] | None = None,
    anchors: InMemoryAnchorStore | None = None,
    **repos: Any,
) -> AuditService:
    return AuditService(
        PatchedUnitOfWorkFactory(base, **repos),
        source or FakeSource(),
        objects or InMemoryObjectStore(),
        clock,
        slices=slices,
        anchors=anchors,
    )


async def test_redelivered_receipts_are_stored_once_and_the_chain_stays_valid(base, clock, serve) -> None:
    audit = service(base, clock, serve=serve)
    first = [draft(seq=i) for i in range(1, 4)]
    assert await audit.write(first) == 3
    later = draft(seq=4)
    assert await audit.write([first[1], later, first[2], later]) == 1
    result = await audit.verify_chain(SPACE_ID, DAY)
    assert result.valid
    assert result.receipts == 4
    assert [h.seq for h in result.heads] == [4]


async def test_large_spaces_spread_receipts_over_slices(base, clock, serve) -> None:
    audit = service(base, clock, slices={SPACE_ID: 16}, serve=serve)
    await audit.write([draft(seq=i, profile_id=uuid4()) for i in range(1, 60)])
    result = await audit.verify_chain(SPACE_ID, DAY)
    assert result.valid
    assert result.receipts == 59
    assert len(result.heads) > 1


async def test_tampering_with_a_stored_receipt_is_detected(base, clock, serve) -> None:
    audit = service(base, clock, serve=serve)
    drafts = [draft(seq=i) for i in range(1, 4)]
    await audit.write(drafts)
    receipts = base.store.module("audit")["receipts"]
    key = (SPACE_ID, drafts[1].receipt_id)
    receipts[key] = dataclasses.replace(
        receipts[key], draft=dataclasses.replace(receipts[key].draft, purpose="sales")
    )
    result = await audit.verify_chain(SPACE_ID, DAY)
    assert not result.valid
    assert [(b.seq, b.reason) for b in result.breaks] == [(2, "content_mismatch")]


async def test_a_truncated_chain_no_longer_matches_its_head(base, clock, serve) -> None:
    audit = service(base, clock, serve=serve)
    drafts = [draft(seq=i) for i in range(1, 4)]
    await audit.write(drafts)
    del base.store.module("audit")["receipts"][(SPACE_ID, drafts[2].receipt_id)]
    result = await audit.verify_chain(SPACE_ID, DAY)
    assert [b.reason for b in result.breaks] == ["head_mismatch"]


async def test_watermarks_follow_producer_contiguity_across_batches(base, clock, serve) -> None:
    audit = service(base, clock, serve=serve)
    one, two, three = (draft(producer="r", seq=i) for i in (1, 2, 3))
    await audit.write([one, three])
    async with base.cell() as uow:
        assert await uow.watermarks.watermark() == one.read_at
    await audit.write([two])
    async with base.cell() as uow:
        assert await uow.watermarks.watermark() == two.read_at
    await audit.write([three])
    async with base.cell() as uow:
        assert await uow.watermarks.watermark() == three.read_at
    await audit.write([], [ProducerMark("r", last_seq=3, at=BASE + timedelta(seconds=4))])
    async with base.cell() as uow:
        assert await uow.watermarks.watermark() == BASE + timedelta(seconds=4)


async def test_a_silent_producer_expires_so_the_cell_watermark_moves(base, clock, serve) -> None:
    audit = service(base, clock, serve=serve)
    await audit.write([draft(producer="gone", seq=1, at=BASE)])
    clock.advance(minutes=5)
    await audit.write([draft(producer="alive", seq=1, at=clock.now())])
    async with base.cell() as uow:
        states = {s.producer_id: s.status.value for s in await uow.watermarks.load_producers()}
        assert states == {"gone": "expired", "alive": "active"}
        assert await uow.watermarks.watermark() == clock.now()


async def test_projections_move_pins_and_agent_watermarks(base, clock, serve) -> None:
    audit = service(base, clock, serve=serve)
    profile = uuid4()
    pinned = draft(
        profile_id=profile,
        conversation_id="c-1",
        pack_hash="ph",
        manifest_hash="mh",
        extra={"view": "chat", "pack_key": "pk", "generation": "7"},
    )
    history = draft(kind=ReceiptKind.HISTORY, profile_id=profile)
    await audit.write([pinned, history])
    assert serve.pins == [
        {
            "conversation_id": "c-1",
            "view": "chat",
            "pack_key": "pk",
            "pack_hash": "ph",
            "generation": 7,
            "pinned_at": pinned.read_at,
        }
    ]
    assert serve.watermarks == [
        {
            "source_id": WHATSAPP_SOURCE,
            "profile_id": profile,
            "manifest_hash": "mh",
            "seen_at": pinned.read_at,
            "thread": "c-1",
            "generation": 7,
        }
    ]


async def test_a_failed_projection_sends_the_batch_back_to_the_queue(base, clock) -> None:
    pin = {"view": "v", "pack_key": "k", "generation": "1"}
    batch = FakeBatch([draft(conversation_id="c-1", pack_hash="ph", extra=pin)])
    await service(base, clock, source=FakeSource(batch), serve=FakeServe(down=True)).run_writer()
    assert batch.retried
    assert not batch.acked
    async with base(SPACE_ID) as uow:
        assert await uow.audit.list_receipts(SPACE_ID, ReceiptQuery()) == []


async def test_the_writer_loop_acks_stored_batches(base, clock, serve) -> None:
    batches = [FakeBatch([draft()]), FakeBatch([draft()], [ProducerMark("read-1", 0, BASE)])]
    await service(base, clock, source=FakeSource(*batches), serve=serve).run_writer()
    assert all(b.acked for b in batches)


async def test_the_anchor_writes_the_daily_root_once(base, clock, serve) -> None:
    objects = InMemoryObjectStore()
    audit = service(base, clock, objects=objects, slices={SPACE_ID: 4}, serve=serve)
    await audit.write([draft(producer="r", seq=i, profile_id=uuid4()) for i in range(1, 9)])
    await audit.write(
        [], [ProducerMark("r", last_seq=8, at=datetime.fromisoformat("2026-09-23T00:00:01+00:00"))]
    )
    task = Task(TaskName.AUDIT_ANCHOR, SPACE_ID, {"day": DAY.isoformat()})
    await audit.audit_anchor(task)
    document = json.loads(objects.objects[f"audit/{SPACE_ID}/{DAY.isoformat()}.json"])
    result = await audit.verify_chain(SPACE_ID, DAY)
    assert result.valid
    assert result.root_matches
    assert document["root"] == result.anchored_root
    objects.objects.clear()
    await audit.audit_anchor(task)
    assert objects.objects == {}


async def anchored_day(audit: AuditService) -> None:
    await audit.write([draft(producer="r", seq=i, profile_id=uuid4()) for i in range(1, 4)])
    await audit.write(
        [], [ProducerMark("r", last_seq=3, at=datetime.fromisoformat("2026-09-23T00:00:01+00:00"))]
    )
    await audit.audit_anchor(Task(TaskName.AUDIT_ANCHOR, SPACE_ID, {"day": DAY.isoformat()}))


async def test_the_root_goes_to_write_once_storage_with_its_retention(base, clock, serve) -> None:
    # The copy sits where not even the cell can delete or replace it, for five years.
    locked = InMemoryAnchorStore()
    objects = InMemoryObjectStore()
    audit = service(base, clock, objects=objects, anchors=locked, serve=serve)
    await anchored_day(audit)
    key = f"audit/{SPACE_ID}/{DAY.isoformat()}.json"
    assert objects.objects == {}
    assert locked.retained_until[key] == datetime(2026, 9, 23, tzinfo=UTC) + timedelta(days=5 * 365)
    async with base(SPACE_ID) as uow:
        root = await uow.audit.get_root(SPACE_ID, DAY)
    assert root is not None
    assert root.location == f"locked:{key}"
    result = await audit.verify_chain(SPACE_ID, DAY)
    assert (result.valid, result.root_matches, result.anchor_copy_matches, result.anchor_locked) == (
        True,
        True,
        True,
        True,
    )


async def test_rewriting_receipts_heads_and_root_row_together_still_breaks_against_the_copy(
    base, clock, serve
) -> None:
    # Someone with the database rewrites the receipts, rebuilds the chain and edits the root row to
    # match: every check inside the database passes, and only the copy outside it tells.
    audit = service(base, clock, anchors=InMemoryAnchorStore(), serve=serve)
    await anchored_day(audit)
    module = base.store.module("audit")
    forged = [dataclasses.replace(r.draft, purpose="sales") for r in module["receipts"].values()]
    module["receipts"].clear()
    module["heads"].clear()
    await audit.write(forged)
    async with base(SPACE_ID) as uow:
        new_root = merkle_root(await uow.audit.day_heads(SPACE_ID, DAY))
    module["roots"][(SPACE_ID, DAY)] = dataclasses.replace(module["roots"][(SPACE_ID, DAY)], root=new_root)
    result = await audit.verify_chain(SPACE_ID, DAY)
    assert (result.breaks, result.root_matches) == ([], True)
    assert (result.anchor_copy_matches, result.valid) == (False, False)


async def test_a_rerun_after_the_copy_was_written_keeps_it(base, clock, serve) -> None:
    locked = InMemoryAnchorStore()
    audit = service(base, clock, anchors=locked, serve=serve)
    await anchored_day(audit)
    key = f"audit/{SPACE_ID}/{DAY.isoformat()}.json"
    copy = locked.objects[key]
    del base.store.module("audit")["roots"][(SPACE_ID, DAY)]
    await audit.audit_anchor(Task(TaskName.AUDIT_ANCHOR, SPACE_ID, {"day": DAY.isoformat()}))
    assert locked.objects[key] == copy
    assert (await audit.verify_chain(SPACE_ID, DAY)).valid


async def test_a_missing_copy_in_the_cell_bucket_fails_verification(base, clock, serve) -> None:
    objects = InMemoryObjectStore()
    audit = service(base, clock, objects=objects, serve=serve)
    await anchored_day(audit)
    assert (await audit.verify_chain(SPACE_ID, DAY)).anchor_locked is False
    objects.objects.clear()
    result = await audit.verify_chain(SPACE_ID, DAY)
    assert (result.anchor_copy_matches, result.valid) == (False, False)


async def test_the_anchor_waits_while_the_receipt_watermark_is_inside_the_day(base, clock, serve) -> None:
    audit = service(base, clock, serve=serve)
    await audit.write([draft(producer="r", seq=1)])
    await audit.audit_anchor(Task(TaskName.AUDIT_ANCHOR, SPACE_ID, {"day": DAY.isoformat()}))
    async with base(SPACE_ID) as uow:
        assert await uow.audit.get_root(SPACE_ID, DAY) is None
        ((scheduled, due_at, _),) = uow.pipeline.scheduled
        assert scheduled.name is TaskName.AUDIT_ANCHOR
        assert due_at > clock.now()


async def test_receipts_list_filters_and_pages(base, clock, serve) -> None:
    audit = service(base, clock, serve=serve)
    profile = uuid4()
    mine = [draft(profile_id=profile) for _ in range(5)]
    await audit.write([*mine, draft(profile_id=uuid4())])
    page = await audit.list_receipts(SPACE_ID, ReceiptQuery(profile_id=profile, limit=3))
    assert [r.receipt_id for r in page.items] == [str(d.receipt_id) for d in reversed(mine[2:])]
    assert page.next_cursor is not None
    rest = await audit.list_receipts(
        SPACE_ID, ReceiptQuery(profile_id=profile, limit=3, before_id=UUID(page.next_cursor))
    )
    assert [r.receipt_id for r in rest.items] == [str(d.receipt_id) for d in reversed(mine[:2])]
    assert rest.next_cursor is None


class FakeCompile:
    def __init__(self, manifests: dict[str, dict[str, Any]]) -> None:
        self.manifests = manifests

    async def get_manifest(self, manifest_hash: str) -> dict[str, Any] | None:
        return self.manifests.get(manifest_hash)


class FakeIngest:
    def __init__(self, events: list[EventRecord]) -> None:
        self.events = {e.event_id: e for e in events}

    async def get_events(self, event_ids: list[UUID]) -> list[EventRecord]:
        return [self.events[i] for i in event_ids if i in self.events]


async def test_lineage_walks_receipt_manifest_items_and_events(base, clock, serve) -> None:
    event = EventRecord(
        event_id=uuid4(),
        kind=EventKind.MESSAGE,
        source_id=WHATSAPP_SOURCE,
        channel="whatsapp",
        speaker=Speaker.CUSTOMER,
        occurred_at=BASE,
        received_at=BASE,
        handle_ids=(uuid4(),),
        text="never leaves the lineage",
        conversation_id="c-9",
    )
    fact = str(uuid4())
    manifest = {
        "items": [{"id": fact, "kind": "fact", "evidence_event_ids": [str(event.event_id)]}],
        "excluded": [{"id": "x", "reason": "policy"}],
    }
    audit = service(
        base, clock, serve=serve, compile=FakeCompile({"mh": manifest}), ingest=FakeIngest([event])
    )
    receipt = draft(manifest_hash="mh", item_ids=(fact,))
    await audit.write([receipt])
    lineage = await audit.receipt_lineage(SPACE_ID, receipt.receipt_id)
    assert lineage.manifest_found
    assert lineage.excluded == [{"id": "x", "reason": "policy"}]
    (item,) = lineage.items
    assert item.kind == "fact"
    assert [e.event_id for e in item.events] == [str(event.event_id)]
    assert "never leaves" not in lineage.model_dump_json()
    with pytest.raises(NotFound):
        await audit.receipt_lineage(SPACE_ID, uuid4())


class FakeIdentity:
    def __init__(self, profiles: dict[UUID, ProfileRecord]) -> None:
        self.profiles = profiles

    async def profile_of(self, handle_id: UUID) -> ProfileRecord | None:
        return self.profiles.get(handle_id)

    async def pseudonyms(self, profile_ids: Sequence[UUID]) -> dict[UUID, str]:
        known = {p.profile_id: p.pseudonym for p in self.profiles.values()}
        return {p: known[p] for p in profile_ids if p in known}


async def test_who_saw_a_fact(base, clock, serve) -> None:
    handle, profile = uuid4(), uuid4()
    identity = FakeIdentity({handle: ProfileRecord(profile, SubjectKind.PERSON, "p-1", 1, 1)})
    audit = service(base, clock, serve=serve, identity=identity)
    fact = str(uuid4())
    saw = [draft(profile_id=profile, item_ids=(fact, "other")), draft(profile_id=profile, item_ids=(fact,))]
    await audit.write([*saw, draft(profile_id=profile, item_ids=("other",))])
    viewers = await audit.fact_viewers(SPACE_ID, UUID(fact), handle)
    assert {r.receipt_id for r in viewers.receipts} == {str(d.receipt_id) for d in saw}
    assert {r.pseudonym for r in viewers.receipts} == {"p-1"}


async def test_a_receipt_names_its_profile_by_pseudonym(base, clock, serve) -> None:
    handle, known, gone = uuid4(), uuid4(), uuid4()
    identity = FakeIdentity({handle: ProfileRecord(known, SubjectKind.PERSON, "p_known", 1, 1)})
    audit = service(
        base, clock, serve=serve, identity=identity, compile=FakeCompile({}), ingest=FakeIngest([])
    )
    receipts = [draft(profile_id=known), draft(profile_id=gone), draft()]
    await audit.write(receipts)

    page = await audit.list_receipts(SPACE_ID, ReceiptQuery())
    lineage = await audit.receipt_lineage(SPACE_ID, receipts[0].receipt_id)

    assert {r.profile_id: r.pseudonym for r in page.items} == {
        str(known): "p_known",
        str(gone): None,
        None: None,
    }
    assert lineage.receipt.pseudonym == "p_known"


async def test_a_receipt_names_the_person_who_acted_and_the_name_stays_out_of_the_chain(
    base, clock, serve
) -> None:
    ana, bruno, unnamed = uuid4(), uuid4(), uuid4()
    audit = service(base, clock, serve=serve, compile=FakeCompile({}), ingest=FakeIngest([]))
    receipts = [
        draft(seq=1, person_id=ana, source_id=None, extra={PERSON_LABEL: "ana@acme.com"}),
        draft(seq=2, person_id=bruno, source_id=None, extra={PERSON_LABEL: "bruno@acme.com"}),
        draft(seq=3, person_id=unnamed, source_id=None),
        draft(seq=4),
    ]
    await audit.write(receipts)

    page = await audit.list_receipts(SPACE_ID, ReceiptQuery())
    lineage = await audit.receipt_lineage(SPACE_ID, receipts[0].receipt_id)

    assert {r.person_id: r.person_label for r in page.items} == {
        str(ana): "ana@acme.com",
        str(bruno): "bruno@acme.com",
        str(unnamed): None,
        None: None,
    }
    assert lineage.receipt.person_label == "ana@acme.com"
    # The name is read beside the chain: the same receipt without it has the same canonical bytes.
    link = {"slice_": 0, "day": BASE.date(), "seq": 1}
    assert canonical(receipts[0], **link) == canonical(dataclasses.replace(receipts[0], extra={}), **link)


async def test_a_person_who_changed_e_mail_is_named_by_the_latest_one(base, clock, serve) -> None:
    ana = uuid4()
    audit = service(base, clock, serve=serve)
    old = draft(seq=1, person_id=ana, extra={PERSON_LABEL: "ana@old.com"})
    new = draft(seq=2, person_id=ana, extra={PERSON_LABEL: "ana@acme.com"})
    await audit.write([new])
    await audit.write([old])  # a late delivery of an earlier action
    page = await audit.list_receipts(SPACE_ID, ReceiptQuery())
    assert {r.person_label for r in page.items} == {"ana@acme.com"}
