"""Receipts: the chain writer, the daily anchor and the governance reads (5.11, 5.13)."""

import dataclasses
import json
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from niadra.app.notify import announce_receipts, enqueue_event, enqueue_receipts
from niadra.contracts.audit import (
    ChainBreakOut,
    ChainHeadOut,
    ChainVerification,
    FactViewers,
    LineageEvent,
    LineageItem,
    ReceiptLineage,
    ReceiptOut,
)
from niadra.contracts.common import Page
from niadra.domain.audit.chain import (
    ChainBreak,
    ChainHead,
    StoredReceipt,
    append,
    merkle_root,
    receipt_day,
    slice_for,
    verify,
)
from niadra.domain.audit.watermarks import (
    ProducerState,
    apply_mark,
    expire_silent,
    record_receipts,
    skip_lost_gap,
)
from niadra.domain.common.errors import Conflict, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.experiments import arms_of_text
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.receipts import PERSON_LABEL, ProducerMark, ReceiptDraft, draft_from_json
from niadra.domain.records import EventRecord
from niadra.domain.serve.records import PIN_TTL, AgentWatermark, ConversationPin
from niadra.domain.tasks import Task
from niadra.domain.vocabulary import ReceiptKind
from niadra.ports.infra import (
    AnchorStore,
    Clock,
    ConfigProvider,
    NoTelemetry,
    ObjectStore,
    ReceiptSource,
    Telemetry,
)
from niadra.ports.repos.audit import AuditRoot, ReceiptQuery
from niadra.ports.turns import TurnIndex
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

ANCHOR_RETRY = timedelta(minutes=10)
# How long a daily root stays undeletable in the locked store; five years, like the cold archive (5.14.0).
ANCHOR_RETENTION = timedelta(days=5 * 365)
# Where an anchor sits is part of its location, so verification reads the copy where it was written.
LOCKED_PREFIX = "locked:"


class AuditService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        source: ReceiptSource,
        objects: ObjectStore,
        clock: Clock,
        *,
        slices: Mapping[UUID, int] | None = None,
        batch_size: int = 500,
        telemetry: Telemetry | None = None,
        anchors: AnchorStore | None = None,
        anchor_retention: timedelta = ANCHOR_RETENTION,
        config: ConfigProvider | None = None,
    ) -> None:
        self._telemetry = telemetry or NoTelemetry()
        self._uow = uow
        self._source = source
        self._objects = objects
        self._clock = clock
        self._slices = dict(slices or {})
        self._batch_size = batch_size
        # Without a locked store the roots go to the cell's bucket.
        self._anchors = anchors
        self._anchor_retention = anchor_retention
        # With the space's configuration the writer streams receipts to the tenant's SIEM (5.13).
        self._config = config
        # The kept tier's index, anchored with the day's receipts (`ports.turns.TurnIndex`, 24.5.4).
        self.turn_index: TurnIndex | None = None

    async def run_writer(self) -> None:
        """The receipt writer loop; a failed batch goes back to the queue whole and is deduplicated again."""
        async for batch in self._source.batches(self._batch_size):
            try:
                await self.write(batch.drafts, batch.marks)
            except Exception:
                log.exception("receipt batch failed; returned to the queue")
                await batch.retry_later()
            else:
                await batch.ack()

    async def write(self, drafts: Sequence[ReceiptDraft], marks: Sequence[ProducerMark] = ()) -> int:
        """Chains and stores a batch, then folds it into the producer watermarks; returns how many are new."""
        first_delivery: dict[UUID, ReceiptDraft] = {}
        for draft in drafts:
            first_delivery.setdefault(draft.receipt_id, draft)
        unique = list(first_delivery.values())
        by_space: dict[UUID, list[ReceiptDraft]] = defaultdict(list)
        for draft in unique:
            by_space[draft.space_id].append(draft)
        stored = 0
        for space_id in sorted(by_space):
            stored += await self._append_space(space_id, by_space[space_id])
        await self._fold_watermarks(unique, marks)
        return stored

    async def record_receipts(self, task: Task) -> None:
        """The durable path (5.11): receipts a read process committed to the outbox because the queue did
        not confirm them in time. At least once: a replay finds them stored and chains nothing twice."""
        drafts = [draft_from_json(d) for d in task.payload.get("drafts", [])]
        await self.write(drafts)

    async def audit_anchor(self, task: Task) -> None:
        """Daily Merkle root over a space's slice heads (5.13), once the cell watermark has passed the day."""
        if "day" in task.payload:
            day = date.fromisoformat(task.payload["day"])
        else:
            day = receipt_day(self._clock.now()) - timedelta(days=1)
        day_end = datetime.combine(day + timedelta(1), time(), UTC)
        # The watermark is the cell's, in the cell database; read it first, in its own transaction. It
        # only moves forward, so a value read a moment before the space transaction is safe to use.
        async with self._uow.cell() as cell:
            watermark = await cell.watermarks.watermark()
        async with self._uow(task.space_id) as uow:
            if await uow.audit.get_root(task.space_id, day) is not None:
                return
            if watermark is not None and watermark < day_end:
                # A fresh id: the queue deduplicates by task id, and this is a new delivery of the same work.
                retry = dataclasses.replace(task, id=uuid7())
                await uow.pipeline.schedule(
                    retry, self._clock.now() + ANCHOR_RETRY, DelayReason.RECEIPT_WATERMARK
                )
                return
            heads = await uow.audit.day_heads(task.space_id, day)
            root = merkle_root(heads)
            key = f"audit/{task.space_id}/{day.isoformat()}.json"
            turns = await self.turn_index.day_index(task.space_id, day) if self.turn_index else None
            document = _anchor_document(task.space_id, day, root, heads, turns)
            location = await self._write_anchor(key, document, root, day_end)
            await uow.audit.save_root(task.space_id, AuditRoot(day, root, location, self._clock.now()))
            if self._config is not None:
                # No second chain (24.5.4): the day's root goes to the company's own, the turn index's too.
                data = _root_event(day, root, heads, turns, location.startswith(LOCKED_PREFIX))
                await enqueue_event(uow, self._config.space(task.space_id), "audit.root", None, data)

    async def _write_anchor(self, key: str, document: bytes, root: bytes, day_end: datetime) -> str:
        """The tamper-proof copy of the root (5.13): in the locked store when there is one, where the
        retention keeps even the cell from deleting or replacing it; else in the cell's bucket."""
        if self._anchors is None:
            await self._objects.put(key, document, "application/json")
            return key
        try:
            await self._anchors.put_anchor(key, document, day_end + self._anchor_retention)
        except Conflict:
            # A rerun after the copy was written but before the root row: it must hold the same root.
            if _root_of(await self._anchors.get_anchor(key)) != root.hex():
                raise
        return LOCKED_PREFIX + key

    async def list_receipts(self, space_id: UUID, query: ReceiptQuery) -> Page[ReceiptOut]:
        async with self._uow(space_id) as uow:
            rows = await uow.audit.list_receipts(space_id, query)
            names = await _names(uow, rows)
        cursor = str(rows[-1].draft.receipt_id) if len(rows) == query.limit else None
        return Page[ReceiptOut](items=[receipt_out(r, names) for r in rows], next_cursor=cursor)

    async def receipt_lineage(self, space_id: UUID, receipt_id: UUID) -> ReceiptLineage:
        async with self._uow(space_id) as uow:
            receipt = await uow.audit.get_receipt(space_id, receipt_id)
            if receipt is None:
                raise NotFound("unknown receipt")
            manifest = None
            if receipt.draft.manifest_hash:
                manifest = await uow.compile.get_manifest(receipt.draft.manifest_hash)
            items = _manifest_items(manifest, receipt.draft.item_ids)
            event_ids = sorted({UUID(e) for item in items for e in item.evidence_event_ids})
            events = {e.event_id: e for e in await uow.ingest.get_events(event_ids)} if event_ids else {}
            names = await _names(uow, [receipt])
        lineage_items = [
            item.model_copy(
                update={
                    "events": [
                        _lineage_event(events[UUID(e)]) for e in item.evidence_event_ids if UUID(e) in events
                    ]
                }
            )
            for item in items
        ]
        excluded = list((manifest or {}).get("excluded", []))
        return ReceiptLineage(
            receipt=receipt_out(receipt, names),
            manifest_found=manifest is not None,
            items=lineage_items,
            excluded=excluded,
        )

    async def fact_viewers(
        self, space_id: UUID, fact_id: UUID, handle_id: UUID | None, limit: int = 100
    ) -> FactViewers:
        async with self._uow(space_id) as uow:
            profile_id = None
            if handle_id is not None:
                profile = await uow.identity.profile_of(handle_id)
                profile_id = profile.profile_id if profile else None
            rows = await uow.audit.receipts_with_item(
                space_id, str(fact_id), profile_id=profile_id, limit=limit
            )
            names = await _names(uow, rows)
        return FactViewers(fact_id=str(fact_id), receipts=[receipt_out(r, names) for r in rows])

    async def verify_chain(self, space_id: UUID, day: date) -> ChainVerification:
        """Recomputes every chain of the day and compares it with the stored heads, the root row and the
        anchored copy outside the database: a rewrite of receipts, heads and root row together still
        breaks against the copy (5.13)."""
        async with self._uow(space_id) as uow:
            receipts = await uow.audit.day_receipts(space_id, day)
            stored_heads = await uow.audit.day_heads(space_id, day)
            anchored = await uow.audit.get_root(space_id, day)
        heads, breaks = verify(space_id, receipts)
        recomputed = {h.slice: h for h in heads}
        for stored in stored_heads:
            if stored.seq and recomputed.get(stored.slice) != stored:
                breaks.append(ChainBreak(stored.slice, stored.seq, "head_mismatch"))
        root = merkle_root(heads)
        root_matches = root == anchored.root if anchored else None
        copy_matches = await self._copy_matches(anchored.location, root) if anchored else None
        return ChainVerification(
            day=day,
            valid=not breaks and root_matches is not False and copy_matches is not False,
            receipts=len(receipts),
            heads=[ChainHeadOut(slice=h.slice, seq=h.seq, hash=h.hash.hex()) for h in heads],
            breaks=[ChainBreakOut(slice=b.slice, seq=b.seq, reason=b.reason) for b in breaks],
            anchored_root=anchored.root.hex() if anchored else None,
            root_matches=root_matches,
            anchor_copy_matches=copy_matches,
            anchor_locked=anchored.location.startswith(LOCKED_PREFIX) if anchored else None,
        )

    async def _copy_matches(self, location: str, root: bytes) -> bool | None:
        """False when the copy is missing or holds another root; None when it cannot be read now."""
        try:
            if location.startswith(LOCKED_PREFIX):
                if self._anchors is None:
                    return None
                data = await self._anchors.get_anchor(location.removeprefix(LOCKED_PREFIX))
            else:
                data = await self._objects.get(location)
        except NotFound:
            return False
        except Exception:
            log.warning("the anchored copy could not be read", exc_info=True)
            return None
        return _root_of(data) == root.hex()

    async def _append_space(self, space_id: UUID, drafts: Sequence[ReceiptDraft]) -> int:
        chains: dict[tuple[int, date], list[ReceiptDraft]] = defaultdict(list)
        slices = self._slices.get(space_id, 1)
        for draft in sorted(drafts, key=lambda d: (d.read_at, d.receipt_id)):
            chains[(slice_for(draft.profile_id, slices), receipt_day(draft.read_at))].append(draft)
        async with self._uow(space_id) as uow:
            # Heads are locked in a fixed order so two writers never deadlock, and the duplicate check runs
            # under the locks: a receipt always maps to the same chain, so no other writer can store it now.
            heads = {key: await uow.audit.lock_head(space_id, *key) for key in sorted(chains)}
            known = await uow.audit.stored_ids(space_id, [d.receipt_id for d in drafts])
            rows: list[StoredReceipt] = []
            for key, head in heads.items():
                fresh = [d for d in chains[key] if d.receipt_id not in known]
                if fresh:
                    linked, new_head = append(head, fresh)
                    rows.extend(linked)
                    await uow.audit.save_head(space_id, new_head)
            if rows:
                await uow.audit.append(space_id, rows)
                await _project(uow, [r.draft for r in rows])
                if self._config is not None:
                    await enqueue_receipts(uow, self._config.space(space_id), rows)
                    await announce_receipts(uow, self._config.space(space_id), rows)
        return len(rows)

    async def _fold_watermarks(self, drafts: Sequence[ReceiptDraft], marks: Sequence[ProducerMark]) -> None:
        now = self._clock.now()
        seqs: dict[str, list[tuple[int, datetime]]] = defaultdict(list)
        for draft in drafts:
            seqs[draft.producer_id].append((draft.producer_seq, draft.read_at))
        async with self._uow.cell() as uow:
            before = {s.producer_id: s for s in await uow.watermarks.load_producers(None)}
            states = dict(before)
            for producer_id, received in seqs.items():
                states[producer_id] = record_receipts(states.get(producer_id), producer_id, received, now)
            for mark in marks:
                if (marked := apply_mark(states.get(mark.producer_id), mark, now)) is not None:
                    states[mark.producer_id] = marked
            states = {pid: self._settle(state, now) for pid, state in states.items()}
            changed = [s for pid, s in states.items() if before.get(pid) != s]
            if changed:
                await uow.watermarks.save_producers(changed)

    def _settle(self, state: ProducerState, now: datetime) -> ProducerState:
        state, lost = skip_lost_gap(state, now)
        if lost:
            self._telemetry.receipts_lost(1)
            log.error(
                "receipts lost: producer %s seq %d-%d never arrived",
                lost.producer_id,
                lost.first_seq,
                lost.last_seq,
            )
        settled = expire_silent(state, now)
        if settled.status is not state.status:
            log.error(
                "receipt producer %s went silent without a final mark; watermark expired", state.producer_id
            )
        return settled


KEPT_GROUPS = frozenset({"memory", "control"})
"""Per customer; `shadow` is per source and is never kept."""


async def _project(uow: UnitOfWork, drafts: Sequence[ReceiptDraft]) -> None:
    """Pins and agent watermarks are derived from receipts; `serve` only moves them forward (5.11). A batch
    writes them in one statement each: the latest (generation, time) of each conversation's pin and of each
    thread's watermark, as the receipts one by one would leave them."""
    pins: dict[tuple[str, str], ConversationPin] = {}
    for draft in drafts:
        if (fields := _pin_fields(draft)) is None:
            continue
        view, pack_key, generation = fields
        assert draft.conversation_id
        assert draft.pack_hash
        pin = ConversationPin(
            draft.conversation_id,
            view,
            pack_key,
            draft.pack_hash,
            generation,
            draft.read_at,
            draft.read_at + PIN_TTL,
        )
        pins[(pin.conversation_id, view)] = _later_pin(pins.get((pin.conversation_id, view)), pin)
    await uow.serve.project_pins(list(pins.values()))
    people: dict[UUID, tuple[str, datetime]] = {}
    for draft in drafts:
        label = draft.extra.get(PERSON_LABEL)
        if (
            draft.person_id
            and label
            and (draft.person_id not in people or people[draft.person_id][1] <= draft.read_at)
        ):
            people[draft.person_id] = (label, draft.read_at)
    if people:
        await uow.audit.name_people(people)
    reads: dict[str, datetime] = {}
    for draft in drafts:
        key = draft.extra.get("pack_key") if draft.kind is ReceiptKind.READ else None
        if key and (key not in reads or reads[key] < draft.read_at):
            reads[key] = draft.read_at
    if reads:
        # Which variants agents really read decides which ones the per-profile cap keeps.
        await uow.compile.mark_read(reads)
    for draft in drafts:
        # The first group a profile got in an experiment stays, whatever a later union does; so does
        # the first arm of an element or agent experiment (6.4), which the read carries in `arms`.
        if not draft.profile_id:
            continue
        arms = arms_of_text(draft.extra.get("arms", ""))
        for experiment, kind, arm in arms:
            await uow.serve.project_assignment(
                experiment=experiment,
                profile_id=draft.profile_id,
                group=arm,
                assigned_at=draft.read_at,
                kind=kind,
            )
        if draft.experiment_group and ":" in draft.experiment_group:
            experiment, group = draft.experiment_group.rsplit(":", 1)
            if group in KEPT_GROUPS and experiment not in {a[0] for a in arms}:
                await uow.serve.project_assignment(
                    experiment=experiment, profile_id=draft.profile_id, group=group, assigned_at=draft.read_at
                )
    marks: dict[tuple[UUID, UUID, str], AgentWatermark] = {}
    for draft in drafts:
        if not (
            draft.kind is ReceiptKind.READ and draft.source_id and draft.profile_id and draft.manifest_hash
        ):
            continue
        mark = AgentWatermark(
            draft.source_id,
            draft.profile_id,
            draft.manifest_hash,
            draft.read_at,
            draft.conversation_id or draft.task_id or "",
            int(draft.extra.get("generation", 0)),
        )
        seen = (mark.source_id, mark.profile_id, mark.thread)
        if (held := marks.get(seen)) is None or (held.generation, held.seen_at) <= (
            mark.generation,
            mark.seen_at,
        ):
            marks[seen] = mark
    await uow.serve.project_watermarks(list(marks.values()))


def _later_pin(held: ConversationPin | None, pin: ConversationPin) -> ConversationPin:
    """What the batch's next receipt does to the pin it holds for the conversation, as the stored row's
    upsert does: a later (generation, time) moves it and keeps the later expiry; an earlier one changes
    nothing."""
    if held is None:
        return pin
    if (held.generation, held.pinned_at) > (pin.generation, pin.pinned_at):
        return held
    return dataclasses.replace(pin, expires_at=max(held.expires_at, pin.expires_at))


def _pin_fields(draft: ReceiptDraft) -> tuple[str, str, int] | None:
    """A pack read in a conversation pins it; the reader sends view, pack key and generation in `extra`."""
    extra = draft.extra
    if not (draft.conversation_id and draft.pack_hash and {"view", "pack_key", "generation"} <= extra.keys()):
        return None
    return extra["view"], extra["pack_key"], int(extra["generation"])


@dataclasses.dataclass(frozen=True, slots=True)
class ReceiptNames:
    """What a person reads in place of the ids a receipt holds; none of it is chained."""

    pseudonyms: Mapping[UUID, str] = dataclasses.field(default_factory=dict)
    people: Mapping[UUID, str] = dataclasses.field(default_factory=dict)


async def _names(uow: UnitOfWork, receipts: Sequence[StoredReceipt]) -> ReceiptNames:
    """One statement for a page's profiles, and one for its people when a person acted in it."""
    people = list(dict.fromkeys(r.draft.person_id for r in receipts if r.draft.person_id))
    return ReceiptNames(
        await uow.identity.pseudonyms(
            list(dict.fromkeys(r.draft.profile_id for r in receipts if r.draft.profile_id))
        ),
        await uow.audit.person_labels(people) if people else {},
    )


def receipt_out(receipt: StoredReceipt, names: ReceiptNames) -> ReceiptOut:
    d = receipt.draft

    def text(value: object) -> str | None:
        return None if value is None else str(value)

    return ReceiptOut(
        receipt_id=str(d.receipt_id),
        kind=d.kind,
        surface=d.surface,
        audience=d.audience,
        read_at=d.read_at,
        profile_id=text(d.profile_id),
        pseudonym=names.pseudonyms.get(d.profile_id) if d.profile_id else None,
        source_id=text(d.source_id),
        person_id=text(d.person_id),
        person_label=names.people.get(d.person_id) if d.person_id else None,
        conversation_id=d.conversation_id,
        task_id=d.task_id,
        object_id=text(d.object_id),
        about_handle_id=text(d.about_handle_id),
        purpose=d.purpose,
        item_ids=list(d.item_ids),
        pack_hash=d.pack_hash,
        manifest_hash=d.manifest_hash,
        verification=d.verification,
        policy_version=d.policy_version,
        experiment_group=d.experiment_group,
        withheld=d.withheld,
        detail=dict(d.detail),
        slice=receipt.slice,
        day=receipt.day,
        seq=receipt.seq,
        prev_hash=receipt.prev_hash.hex(),
        hash=receipt.hash.hex(),
    )


def _manifest_items(manifest: dict[str, Any] | None, delivered: Sequence[str]) -> list[LineageItem]:
    """Items the receipt delivered, enriched with the manifest's evidence when the manifest lists them."""
    listed = {str(i.get("id")): i for i in (manifest or {}).get("items", []) if isinstance(i, dict)}
    items = []
    for item_id in delivered:
        entry = listed.get(item_id, {})
        evidence = [str(e) for e in entry.get("evidence_event_ids", [])]
        items.append(LineageItem(id=item_id, kind=entry.get("kind"), evidence_event_ids=evidence))
    return items


def _lineage_event(event: EventRecord) -> LineageEvent:
    return LineageEvent(
        event_id=str(event.event_id),
        kind=event.kind,
        source_id=str(event.source_id),
        channel=event.channel,
        occurred_at=event.occurred_at,
        conversation_id=event.conversation_id,
        task_id=event.task_id,
    )


def _root_of(document: bytes) -> str | None:
    try:
        root = json.loads(document).get("root")
    except (ValueError, AttributeError):
        return None
    return str(root) if root else None


def _anchor_document(
    space_id: UUID, day: date, root: bytes, heads: Sequence[ChainHead], turns: tuple[int, bytes] | None
) -> bytes:
    document: dict[str, Any] = {
        "space_id": str(space_id),
        "day": day.isoformat(),
        "root": root.hex(),
        "heads": [{"slice": h.slice, "seq": h.seq, "hash": h.hash.hex()} for h in heads],
    }
    if turns is not None:
        document["turns"] = {"count": turns[0], "root": turns[1].hex()}
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def _root_event(
    day: date, root: bytes, heads: Sequence[ChainHead], turns: tuple[int, bytes] | None, locked: bool
) -> dict[str, Any]:
    """`audit.root`: what the day's anchor holds, digests and counts only."""
    return {
        "day": day.isoformat(),
        "root": root.hex(),
        "heads": [{"slice": h.slice, "seq": h.seq, "hash": h.hash.hex()} for h in heads],
        "turns": {"count": turns[0], "root": turns[1].hex()} if turns is not None else None,
        "locked": locked,
    }
