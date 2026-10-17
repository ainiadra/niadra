"""Knowledge gaps and standing answers (13, 6.5): counts by subject and category, never a customer id, a
value or a text; the sources that could record each gap come from the space's own mappings and schema."""

import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from niadra.app.insights import InsightsService
from niadra.config.models import CategoryRule, Mapping, MappingRule, ObjectExpr, Policy
from niadra.domain.audit.chain import StoredReceipt
from niadra.domain.common.errors import Forbidden
from niadra.domain.common.ids import uuid7
from niadra.domain.insights.gaps import (
    EXACT_LIMIT,
    IDENTIFIER,
    AskedQuestion,
    GapDay,
    GapKey,
    GapKind,
    MappedType,
    Sketch,
    SourceHint,
    question_gaps,
    report,
    slot_signals,
    source_hints,
)
from niadra.domain.measure.model import MeasuredSession
from niadra.domain.receipts import ReceiptDraft
from niadra.domain.serve.turn import VALUE_TYPES
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface, Sensitivity
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, sample_config
from tests.unit.insights.conftest import LIMITS, RecordingSink, person

DAY = date(2026, 9, 21)
SALT = SPACE_ID.bytes


def why(*derived: dict[str, object]) -> str:
    return json.dumps({"items": [], "derived": list(derived)})


def analysis(*types: str) -> str:
    return json.dumps({"question": "what", "asked_types": list(types)})


def _fixed_ids(n: int, *, start: int = 0) -> list[UUID]:
    """Known-distinct ids, fixed by construction: no hash draw, no flake from a rare collision."""
    return [UUID(int=start + i) for i in range(n)]


def test_the_sketch_counts_each_customer_once_and_merges_days() -> None:
    first, second = Sketch(), Sketch()
    people = _fixed_ids(3000)
    for p in people[:2000]:
        first.add(SALT, p)
        first.add(SALT, p)
    for p in people[1000:]:
        second.add(SALT, p)
    assert abs(first.count() - 2000) < 100
    assert abs(first.merge(second).count() - 3000) < 150
    few = Sketch()
    for p in people[:7]:
        few.add(SALT, p)
    assert few.count() == 7
    assert Sketch(few.to_bytes()).count() == 7
    assert Sketch().count() == 0


def test_the_sketch_is_exact_up_to_its_limit_and_estimates_past_it() -> None:
    """A sketch of 2^12 registers can, by a rare collision, be off by one at a small cardinality (the bug
    this guards: knowledge-gaps counts of a handful of customers must never move between runs). Below
    `EXACT_LIMIT` the sketch keeps an exact set of salted digests, so the count is always right; only past
    the limit does it fall back to the HyperLogLog estimate."""
    at_limit = Sketch()
    for member in _fixed_ids(EXACT_LIMIT):
        at_limit.add(SALT, member)
    assert at_limit.count() == EXACT_LIMIT
    assert Sketch(at_limit.to_bytes()).count() == EXACT_LIMIT  # the exact set round-trips through storage

    past_limit = Sketch()
    for member in _fixed_ids(EXACT_LIMIT + 1):
        past_limit.add(SALT, member)
    assert abs(past_limit.count() - (EXACT_LIMIT + 1)) < 100  # now an estimate, no exactness promised

    # Merging two exact sketches whose union stays within the limit is still exact.
    left, right = Sketch(), Sketch()
    for member in _fixed_ids(5):
        left.add(SALT, member)
    for member in _fixed_ids(5, start=5):
        right.add(SALT, member)
    assert left.merge(right).count() == 10

    # Merging an exact sketch with one that has already given up exactness falls back to the estimate.
    merged = left.merge(past_limit)
    assert abs(merged.count() - (EXACT_LIMIT + 1)) < 100

    # A sketch loaded from bytes with no trailer (every row written before this fix) stays approximate,
    # never claiming an exactness the stored bytes cannot back up: this is the very estimate error the
    # exact set exists to avoid, so it need not (and here does not) land on the true count of 256.
    legacy = Sketch(bytes(at_limit.registers))
    assert legacy.exact is None
    assert abs(legacy.count() - EXACT_LIMIT) < 20
    legacy.add(SALT, UUID(int=999_999))  # adding more never raises, and stays approximate
    assert abs(legacy.count() - (EXACT_LIMIT + 1)) < 20


def test_a_slots_receipt_says_what_was_missing_or_answered() -> None:
    missing = {"why": why({"kind": "no_record", "rule": "no_record", "asked_types": ["invoice"]})}
    assert slot_signals({**missing, "analysis": analysis("invoice")}) == [(GapKind.NO_RECORD, "invoice")]
    number_only = {"why": why({"kind": "no_record", "rule": "no_record", "asked_types": []})}
    assert slot_signals(number_only) == [(GapKind.NO_RECORD, IDENTIFIER)]
    answered = {"why": why(), "analysis": analysis("protocol", "order")}
    assert slot_signals(answered) == [(GapKind.ANSWERED, "protocol"), (GapKind.ANSWERED, "order")]
    # Withheld until verification is not something memory lacks.
    withheld = {"why": why({"kind": "withheld", "asked_types": ["invoice"]}), "analysis": analysis("invoice")}
    assert slot_signals(withheld) == []
    assert slot_signals({"why": "not json"}) == []
    assert slot_signals(None) == []


def test_an_agent_question_is_a_gap_only_for_a_customer_memory_knew() -> None:
    at = datetime(2026, 9, 21, 10, tzinfo=UTC)
    asked = [AskedQuestion("address", at), AskedQuestion("preferred_name", at)]
    known = {"preferred_name": at - timedelta(days=3), "address": at + timedelta(minutes=5)}
    assert question_gaps(asked, known, returning=True) == ["address"]
    assert question_gaps(asked, known, returning=False) == []


def _day(kind: GapKind, subject: str, topic: str, customers: int, turns: int | None = None) -> GapDay:
    row = GapDay(DAY, GapKey(kind, subject, topic), turns if turns is not None else customers)
    for member in _fixed_ids(customers):
        row.customers.add(SALT, member)
    return row


def test_the_report_withholds_small_groups_and_proposes_standing_answers() -> None:
    rows = [
        _day(GapKind.NO_RECORD, "refund", "billing", 12, 20),
        _day(GapKind.NO_RECORD, "invoice", "billing", 11, 10),
        _day(GapKind.ANSWERED, "invoice", "billing", 30, 90),
        _day(GapKind.ANSWERED, "protocol", "support", 25, 99),
        _day(GapKind.NO_RECORD, "protocol", "support", 1, 1),
        _day(GapKind.AGENT_QUESTION, "address", "delivery", 3),
    ]
    hints = {"invoice": [SourceHint(str(BILLING_SOURCE), "billing-agent", "mapping")]}
    found = report(
        rows, since=DAY, until=DAY + timedelta(days=1), min_customers=10, hints=hints, language="pt-BR"
    )
    assert [(g.subject, g.customers, g.turns) for g in found.gaps] == [
        ("refund", 12, 20),
        ("invoice", 11, 10),
    ]
    assert found.gaps[1].answered_share == 0.9
    assert found.gaps[1].could_record == tuple(hints["invoice"])
    assert found.withheld_groups == 2
    assert [(r.subject, r.answered_share) for r in found.recurring] == [("invoice", 0.9), ("protocol", 0.99)]
    kinds = [(p.kind, p.subject) for p in found.proposals]
    # Refund: nothing records it. Invoice is mapped, so its missing rows are not structural.
    assert kinds == [("not_recorded", "refund"), ("settled", "invoice"), ("settled", "protocol")]
    assert "Nenhum sistema envia refund" in found.proposals[0].text


def test_the_sources_that_could_record_a_subject_come_from_mappings_and_the_schema() -> None:
    mapped = [
        MappedType("s1", "erp", ("invoice.issued", "invoice", "amount_due")),
        MappedType("s2", "crm", ("ticket.opened",)),
    ]
    hints = source_hints(
        ["invoice", "ticket", "claim", "address"], mapped, {"address": [("s3", "chat")]}, VALUE_TYPES
    )
    assert hints["invoice"] == [SourceHint("s1", "erp", "mapping")]
    assert hints["ticket"] == [SourceHint("s2", "crm", "mapping")]
    assert hints["claim"] == []
    assert hints["address"] == [SourceHint("s3", "chat", "schema")]


def _receipt(profile: UUID, conversation: str, detail: dict[str, str], at: datetime) -> StoredReceipt:
    draft = ReceiptDraft(
        receipt_id=uuid7(),
        space_id=SPACE_ID,
        producer_id="read-1",
        producer_seq=1,
        kind=ReceiptKind.READ,
        surface=ReceiptSurface.SLOTS,
        audience=Audience.CUSTOMER_AGENT,
        read_at=at,
        profile_id=profile,
        source_id=WHATSAPP_SOURCE,
        conversation_id=conversation,
        detail=detail,
    )
    return StoredReceipt(draft, 0, at.date(), 1, b"", b"")


async def _space_with_gaps(cell: Cell) -> InsightsService:
    config = sample_config()
    mapping = Mapping(
        source_id=BILLING_SOURCE,
        version="m1",
        rules=[
            MappingRule(
                match="type",
                canonical_type="invoice.issued",
                idempotency_key="id",
                occurred_at="at",
                object_refs=[ObjectExpr(type="invoice", namespace="erp", id="number")],
            )
        ],
    )
    policy = Policy(version="p1", categories=[CategoryRule(name="health", sensitivity=Sensitivity.SENSITIVE)])
    cell.config.put(config.model_copy(update={"mappings": [mapping], "policy": policy}))
    at = datetime.combine(DAY, datetime.min.time(), tzinfo=UTC) + timedelta(hours=14)
    receipts = []
    for n in range(12):
        profile, conversation = uuid7(), f"wa-{n}"
        topic = "health" if n >= 9 else "billing"
        async with cell.uow(SPACE_ID) as uow:
            await uow.measure.put_session(
                MeasuredSession(
                    uuid7(),
                    "m3",
                    conversation,
                    WHATSAPP_SOURCE,
                    "whatsapp",
                    None,
                    "",
                    DAY,
                    at,
                    at,
                    False,
                    topic,
                )
            )
        refund = {
            "why": why({"kind": "no_record", "asked_types": ["refund"]}),
            "analysis": analysis("refund"),
        }
        receipts += [_receipt(profile, conversation, refund, at), _receipt(profile, conversation, refund, at)]
        if n < 5:
            invoice = {
                "why": why({"kind": "no_record", "asked_types": ["invoice"]}),
                "analysis": analysis("invoice"),
            }
            receipts.append(_receipt(profile, conversation, invoice, at))
    async with cell.uow(SPACE_ID) as uow:
        await uow.audit.append(SPACE_ID, receipts)
    assert cell.clock.now().date() == DAY + timedelta(days=1)
    return InsightsService(
        cell.uow, cell.keys, cell.clock, cell.config, RecordingSink(), cell.models, cell.cache, LIMITS
    )


async def test_the_nightly_pass_counts_gaps_by_subject_and_category(cell: Cell) -> None:
    service = await _space_with_gaps(cell)
    assert await service.record_gaps(SPACE_ID) >= 1
    assert await service.record_gaps(SPACE_ID) == 0  # once a day

    found = await service.gaps(person("analysis"))
    refund = next(g for g in found.gaps if g.subject == "refund")
    assert (refund.topic, refund.customers, refund.turns, refund.could_record) == ("billing", 9, 18, ())
    invoice = next(g for g in found.gaps if g.subject == "invoice")
    assert invoice.could_record == (SourceHint(str(BILLING_SOURCE), "billing-agent", "mapping"),)
    # The health conversations are a category the analyst policy denies: counted as withheld, never shown.
    assert all(g.topic != "health" for g in found.gaps)
    assert found.withheld_groups >= 1
    assert [p.subject for p in found.proposals if p.kind == "not_recorded"] == ["refund"]


async def test_the_report_names_no_customer(cell: Cell) -> None:
    service = await _space_with_gaps(cell)
    await service.record_gaps(SPACE_ID)
    found = await service.gaps(person("analysis"))
    async with cell.uow(SPACE_ID) as uow:
        stored = await uow.audit.slot_receipts(SPACE_ID, DAY, after=None, limit=100)
    text = repr(found)
    assert stored
    assert not any(
        str(r.draft.profile_id) in text or (r.draft.conversation_id or "-") in text for r in stored
    )


async def test_the_team_reviews_a_gap_and_only_analysts_read_them(cell: Cell) -> None:
    service = await _space_with_gaps(cell)
    await service.record_gaps(SPACE_ID)
    analyst = person("analysis")
    found = await service.gaps(analyst)
    key = next(p.key for p in found.proposals)
    await service.review_gap(analyst, key, "accepted", "connect the refunds table of the ERP")
    again = await service.gaps(analyst)
    assert again.reviews[key][0] == "accepted"
    with pytest.raises(Forbidden):
        await service.gaps(person("security"))
