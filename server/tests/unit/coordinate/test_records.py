"""A batch of coordination records folds so each row is written once (24.3.4): an effect keeps its latest
state whatever order its records came in, a release or a lift joins the claim or entry of the same batch, a
contact joins its uses, and a spend keeps its settlement; the queue carries them as JSON and back."""

from dataclasses import replace
from uuid import UUID

from niadra.adapters.outbound.rabbitmq.coordination import decode_records, encode_records
from niadra.domain.coordinate.model import EffectSnapshot, EffectState, Settler
from niadra.domain.coordinate.records import (
    ContactRecord,
    CoordinationRecord,
    EffectRecord,
    LiftRecord,
    OwnershipRecord,
    ReleaseRecord,
    SpendRecord,
    SuppressionRecord,
    by_space,
)

SPACE = UUID(int=1)
OTHER = UUID(int=2)
SOURCE = UUID(int=3)
RESERVED = EffectSnapshot(
    state=EffectState.RESERVED,
    attempt=1,
    kind="delivery",
    source=str(SOURCE),
    agent="agent:closing",
    reserved_at_ms=1000,
    lease_until_ms=61_000,
    settled_at_ms=None,
    settled_by=None,
    done_at_ms=None,
    cost_units=0,
    fact="conversation\twa\tc-1",
    ttl_ms=86_400_000,
)
DONE = replace(
    RESERVED, state=EffectState.DONE, settled_at_ms=2000, settled_by=Settler.RESERVER, done_at_ms=2000
)
CLAIM = OwnershipRecord(
    SPACE, UUID(int=10), b"h" * 32, None, "owner", "agent:sales", "agent_active", "declaration", SOURCE,
    1000, 9000, ("promo",), 1000,
)  # fmt: skip
ENTRY = SuppressionRecord(
    SPACE,
    UUID(int=20),
    "phone",
    b"h" * 32,
    b"sealed",
    1,
    "marketing",
    None,
    "opt_out",
    SOURCE,
    1000,
    None,
    None,
)


def test_an_effect_keeps_its_latest_state_whatever_order_it_came_in() -> None:
    [batch] = by_space([EffectRecord(SPACE, b"k", DONE, SOURCE), EffectRecord(SPACE, b"k", RESERVED, SOURCE)])
    assert [e.snapshot.state for e in batch.effects] == [EffectState.DONE]
    retry = replace(RESERVED, attempt=2, reserved_at_ms=5000)
    [batch] = by_space([EffectRecord(SPACE, b"k", DONE, SOURCE), EffectRecord(SPACE, b"k", retry, SOURCE)])
    assert [e.snapshot.attempt for e in batch.effects] == [2]


def test_a_release_and_a_lift_join_what_they_end_and_each_space_is_its_own_batch() -> None:
    records: list[CoordinationRecord] = [
        CLAIM,
        ReleaseRecord(SPACE, CLAIM.claim_id, 5000),
        ReleaseRecord(SPACE, UUID(int=11), 5000),
        ENTRY,
        LiftRecord(SPACE, ENTRY.suppression_id, 6000),
        EffectRecord(OTHER, b"k", RESERVED, SOURCE),
    ]
    first, second = by_space(records)
    assert [c.released_at_ms for c in first.claims] == [5000]
    assert [r.claim_id for r in first.releases] == [UUID(int=11)]
    assert [s.lifted_at_ms for s in first.suppressions] == [6000]
    assert first.lifts == ()
    assert (second.space_id, second.size) == (OTHER, 1)


CONTACT = ContactRecord(
    SPACE, UUID(int=30), b"h" * 32, "marketing", "whatsapp", SOURCE, "agent:sales", "wa_gateway",
    UUID(int=30), "reserved", reserved_at_ms=1000,
)  # fmt: skip
SPEND = SpendRecord(
    SPACE, UUID(int=30), "c:marketing", "c:marketing:promo", "subject", b"h" * 32, 1, 1000, 9000
)


def test_a_contact_joins_its_uses_and_a_spend_keeps_its_settlement() -> None:
    used = replace(CONTACT, state="made", reserved_at_ms=None, used_at_ms=3000, use_ids=(UUID(int=31),))
    again = replace(used, used_at_ms=4000, use_ids=(UUID(int=32),))
    settled = replace(SPEND, units=0, settled=True)
    [batch] = by_space([used, CONTACT, again, settled, SPEND])
    [contact] = batch.contacts
    assert (contact.state, contact.reserved_at_ms, contact.used_at_ms) == ("made", 1000, 4000)
    assert contact.use_ids == (UUID(int=31), UUID(int=32))
    assert [(s.units, s.settled) for s in batch.spends] == [(0, True)]


def test_the_queue_carries_every_kind_of_record_as_it_was() -> None:
    records: list[CoordinationRecord] = [
        EffectRecord(SPACE, b"\x00\xff" * 16, DONE, SOURCE),
        CLAIM,
        ReleaseRecord(SPACE, CLAIM.claim_id, 5000),
        ENTRY,
        LiftRecord(SPACE, ENTRY.suppression_id, 6000),
        replace(CONTACT, use_ids=(UUID(int=31),)),
        SPEND,
    ]
    assert decode_records(encode_records(records)) == records
