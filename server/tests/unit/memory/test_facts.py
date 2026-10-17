from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from niadra.domain.common.ids import uuid7
from niadra.domain.memory.facts import (
    contention_group,
    decayed,
    merge_observation,
    normalize_value,
    ref,
    retire,
    settle,
)
from niadra.domain.records import Fact
from niadra.domain.vocabulary import FactStatus, Speaker

T0 = datetime(2026, 1, 1, tzinfo=UTC)
PHONE = UUID("0192f5a0-0000-7000-8000-0000000000a1")
EMAIL = UUID("0192f5a0-0000-7000-8000-0000000000a2")


def seen(
    value: str,
    day: int,
    *,
    quarantined: bool = False,
    speaker: Speaker = Speaker.CUSTOMER,
    origin: UUID = PHONE,
    predicate: str = "preferred_name",
) -> Fact:
    at = T0 + timedelta(days=day)
    return Fact(
        fact_id=uuid7(),
        origin_handle_id=origin,
        predicate=predicate,
        value=value,
        category="contact",
        sensitivity="normal",
        speaker=speaker,
        confidence=0.8,
        valid_at=at,
        created_at=at,  # type: ignore[arg-type]
        status=FactStatus.QUARANTINED if quarantined else FactStatus.ACTIVE,
        last_confirmed_at=at,
    )


class Slot:
    """What `reconcile` does for one single-valued slot, without the database."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], Fact] = {}

    def add(self, fact: Fact) -> None:
        key = (fact.origin_handle_id, normalize_value(fact.value))
        self.rows[key] = merge_observation(self.rows[key], fact) if key in self.rows else fact
        self._settle()

    def retire(self, value: str, day: int) -> None:
        key = (PHONE, normalize_value(value))
        if key in self.rows and (done := retire(self.rows[key], T0 + timedelta(days=day))):
            self.rows[key] = done
        self._settle()

    def _settle(self) -> None:
        rows = list(self.rows.values())
        wanted = settle(contention_group(rows[0], rows, [], lambda f: True))
        self.rows = {
            k: replace(f, status=wanted[ref(f)][0], invalid_at=wanted[ref(f)][1]) if ref(f) in wanted else f
            for k, f in self.rows.items()
        }

    def state(self) -> dict[tuple[UUID, str], tuple[FactStatus, datetime, datetime, datetime | None]]:
        return {k: (f.status, f.valid_at, f.last_confirmed_at, f.invalid_at) for k, f in self.rows.items()}


def test_newer_value_wins_and_the_old_one_expires_when_the_new_began() -> None:
    slot = Slot()
    slot.add(seen("Ana", 1))
    slot.add(seen("Bia", 5))
    state = slot.state()
    assert state[(PHONE, "ana")][0] is FactStatus.EXPIRED
    assert state[(PHONE, "ana")][3] == T0 + timedelta(days=5)
    assert state[(PHONE, "bia")][0] is FactStatus.ACTIVE


def test_an_old_event_processed_late_is_born_expired() -> None:
    slot = Slot()
    slot.add(seen("Bia", 5))
    slot.add(seen("Ana", 1))
    assert slot.state()[(PHONE, "ana")][:1] == (FactStatus.EXPIRED,)
    assert slot.state()[(PHONE, "bia")][0] is FactStatus.ACTIVE


def test_a_value_seen_again_comes_back_and_dedupes() -> None:
    slot = Slot()
    for value, day in [("Ana", 1), ("Bia", 2), ("ana ", 3)]:
        slot.add(seen(value, day))
    state = slot.state()
    assert len(state) == 2
    assert state[(PHONE, "ana")] == (FactStatus.ACTIVE, T0 + timedelta(days=1), T0 + timedelta(days=3), None)
    assert state[(PHONE, "bia")][3] == T0 + timedelta(days=3)


def test_retire_ends_a_value_until_it_is_said_again() -> None:
    slot = Slot()
    slot.add(seen("Ana", 1))
    slot.retire("Ana", 2)
    assert slot.state()[(PHONE, "ana")][0] is FactStatus.RETRACTED
    slot.add(seen("Ana", 3))
    assert slot.state()[(PHONE, "ana")][0] is FactStatus.ACTIVE


def test_a_retired_value_does_not_revive_the_older_one() -> None:
    slot = Slot()
    slot.add(seen("Ana", 1))
    slot.add(seen("Bia", 2))
    slot.retire("Bia", 3)
    state = slot.state()
    assert state[(PHONE, "bia")][0] is FactStatus.RETRACTED
    assert state[(PHONE, "ana")][0] is FactStatus.EXPIRED


def test_quarantined_observations_never_contend_nor_move_a_clean_row() -> None:
    slot = Slot()
    slot.add(seen("Ana", 1))
    slot.add(seen("Admin", 9, quarantined=True))
    slot.add(seen("Ana", 20, quarantined=True))
    state = slot.state()
    assert state[(PHONE, "ana")] == (FactStatus.ACTIVE, T0 + timedelta(days=1), T0 + timedelta(days=1), None)
    assert state[(PHONE, "admin")][0] is FactStatus.QUARANTINED


def test_same_value_on_two_handles_is_one_claim() -> None:
    slot = Slot()
    slot.add(seen("Ana", 1, origin=PHONE))
    slot.add(seen("Ana", 2, origin=EMAIL))
    assert {s[0] for s in slot.state().values()} == {FactStatus.ACTIVE}


def test_customer_and_system_never_expire_each_other() -> None:
    said = seen("0823 em aberto", 1, predicate="invoice_status")
    recorded = seen("0823 paga", 2, predicate="invoice_status", speaker=Speaker.SYSTEM)
    group = contention_group(said, [said, recorded], [(ref(said), ref(recorded))], lambda f: True)
    assert group == [said]


def test_multi_valued_facts_contend_only_through_contradicts() -> None:
    a, b, c = (seen(v, d, predicate="disputed_invoice") for v, d in [("0823", 1), ("0824", 2), ("0825", 3)])
    assert contention_group(a, [a, b, c], [], lambda f: False) == [a]
    group = contention_group(c, [a, b, c], [(ref(c), ref(a))], lambda f: False)
    assert {ref(f) for f in group} == {ref(a), ref(c)}
    assert settle(group)[ref(a)] == (FactStatus.EXPIRED, T0 + timedelta(days=3))


def test_decay_is_a_ceiling_by_age_and_idempotent() -> None:
    fact = replace(seen("Ana", 0), confidence=0.9)
    now = T0 + timedelta(days=180)
    once = decayed(fact, now)
    assert once == 0.5
    assert decayed(replace(fact, confidence=once), now) == once
    assert decayed(replace(fact, speaker=Speaker.SYSTEM), now) == 0.9


def test_normalization_ignores_case_accents_spacing_and_edge_punctuation() -> None:
    assert normalize_value("  São   Paulo. ") == normalize_value("sao paulo") == "sao paulo"


OBSERVATIONS = st.lists(
    st.tuples(st.sampled_from(["Ana", "Bia", "Cris"]), st.integers(0, 30), st.booleans()),
    min_size=1,
    max_size=8,
)


@settings(max_examples=200, deadline=None)
@given(observations=OBSERVATIONS, data=st.data())
def test_out_of_order_processing_converges_to_the_same_state(
    observations: list[tuple[str, int, bool]], data: st.DataObject
) -> None:
    facts = [seen(v, d, quarantined=q) for v, d, q in observations]
    shuffled = data.draw(st.permutations(facts))
    in_order, out_of_order = Slot(), Slot()
    for fact in facts:
        in_order.add(fact)
    for fact in shuffled:
        out_of_order.add(fact)
    assert in_order.state() == out_of_order.state()
    assert sum(1 for s in in_order.state().values() if s[0] is FactStatus.ACTIVE) <= 1


@settings(max_examples=200, deadline=None)
@given(
    observations=OBSERVATIONS,
    retires=st.lists(st.tuples(st.sampled_from(["Ana", "Bia"]), st.integers(0, 30)), max_size=3),
    data=st.data(),
)
def test_retires_commute_with_later_observations(
    observations: list[tuple[str, int, bool]], retires: list[tuple[str, int]], data: st.DataObject
) -> None:
    # Retires only name rows the extraction saw, so every value exists before the shuffled part.
    first = [seen("Ana", 0), seen("Bia", 0)]
    steps = [("add", seen(v, d, quarantined=q)) for v, d, q in observations] + [
        ("retire", r) for r in retires
    ]
    shuffled = data.draw(st.permutations(steps))
    results = []
    for order in (steps, shuffled):
        slot = Slot()
        for fact in first:
            slot.add(fact)
        for kind, step in order:
            if kind == "add":
                slot.add(step)  # type: ignore[arg-type]
            else:
                slot.retire(*step)  # type: ignore[misc]
        results.append(slot.state())
    assert results[0] == results[1]
