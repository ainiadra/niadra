"""What stops a restore and what a restore changes (WP-G), as pure functions."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.domain.common.ids import uuid7
from niadra.domain.memory.facts import Relation, ref
from niadra.domain.memory.restore import Refusal, is_correction_of, refusal, replacing, restored
from niadra.domain.records import Fact
from niadra.domain.vocabulary import FactStatus, RelationType, Sensitivity, Speaker

T0 = datetime(2026, 9, 20, 12, tzinfo=UTC)
HANDLE = UUID("0192f5a0-0000-7000-8000-0000000000f1")
LINKED = UUID("0192f5a0-0000-7000-8000-0000000000f2")
SAID, RETRACT, CORRECT = uuid7(), uuid7(), uuid7()


def fact(
    value: str, *evidence: UUID, status: FactStatus = FactStatus.RETRACTED, handle: UUID = HANDLE
) -> Fact:
    return Fact(
        fact_id=uuid7(),
        origin_handle_id=handle,
        predicate="preferred_name",
        value=value,
        category="contact",
        sensitivity=Sensitivity.NORMAL,
        speaker=Speaker.CUSTOMER,
        confidence=0.8,
        valid_at=T0,
        created_at=T0,
        status=status,
        last_confirmed_at=T0,
        invalid_at=T0 + timedelta(days=1) if status is FactStatus.RETRACTED else None,
        expired_at=T0 + timedelta(days=1) if status is FactStatus.RETRACTED else None,
        evidence_event_ids=evidence,
    )


def why(
    target: Fact, *, provisional: tuple[UUID, ...] = (), replaced_by: tuple[Fact, ...] = ()
) -> Refusal | None:
    return refusal(
        target, provisional=provisional, feedback_event_ids={RETRACT, CORRECT}, replaced_by=replaced_by
    )


def test_a_retracted_fact_with_the_customers_evidence_can_come_back() -> None:
    assert why(fact("Rafa", SAID, RETRACT)) is None


def test_only_a_retracted_fact_is_restored() -> None:
    for status in (FactStatus.ACTIVE, FactStatus.EXPIRED, FactStatus.QUARANTINED):
        assert why(fact("Rafa", SAID, status=status)) is Refusal.NOT_RETRACTED


def test_a_fact_on_an_unconfirmed_link_is_refused() -> None:
    assert why(fact("Bia", SAID, RETRACT, handle=LINKED), provisional=(LINKED,)) is Refusal.UNCONFIRMED_LINK


def test_a_fact_left_with_only_the_companys_feedback_was_erased() -> None:
    assert why(fact("Rafa", RETRACT)) is Refusal.ERASED
    assert why(fact("Rafa")) is Refusal.ERASED


def test_a_correction_a_restore_undid_is_undone_not_erased() -> None:
    restore = uuid7()
    corrected = fact("Rafaela", CORRECT, restore)
    assert (
        refusal(
            corrected,
            provisional=(),
            feedback_event_ids={CORRECT, restore},
            replaced_by=(),
            undone_by={restore},
        )
        is Refusal.UNDONE
    )
    # A restore event that is not among its evidence says nothing about it.
    assert (
        refusal(
            fact("Rafa", RETRACT),
            provisional=(),
            feedback_event_ids={RETRACT},
            replaced_by=(),
            undone_by={restore},
        )
        is Refusal.ERASED
    )


def test_a_fact_merged_into_another_stays_merged() -> None:
    target = fact("Rafa", SAID, RETRACT)
    kept = fact("Rafa S.", SAID, uuid7(), status=FactStatus.ACTIVE)
    record = fact("Rafael", uuid7(), status=FactStatus.ACTIVE)
    assert why(target, replaced_by=(kept,)) is Refusal.MERGED
    # A system record that took over the slot is not a merge: the restore settles against it.
    assert why(target, replaced_by=(record,)) is None


def test_the_correction_of_the_fact_is_told_apart_by_its_event() -> None:
    target = fact("Rafa", SAID, CORRECT)
    corrected = fact("Rafaela", CORRECT, status=FactStatus.ACTIVE)
    other = fact("Rafaela", uuid7(), status=FactStatus.ACTIVE)
    events = {CORRECT: ("correct_fact", str(target.fact_id))}
    assert is_correction_of(target, corrected, events)
    assert not is_correction_of(target, other, events)
    assert not is_correction_of(fact("Bia", SAID), corrected, events)


def test_replacing_lists_the_supersessions_of_the_fact() -> None:
    target = fact("Rafa", SAID)
    newer = fact("Rafaela", CORRECT, status=FactStatus.ACTIVE)
    unrelated = fact("Bia", SAID)
    edges = [
        Relation(ref(newer), ref(target), RelationType.SUPERSEDES),
        Relation(ref(target), ref(newer), RelationType.SUPERSEDES),
        Relation(ref(unrelated), ref(target), RelationType.CONTRADICTS),
    ]
    assert replacing(target, edges) == [ref(newer)]


def test_restored_keeps_the_fact_and_confirms_it_now() -> None:
    target = fact("Rafa", SAID, RETRACT)
    later = T0 + timedelta(days=2)
    back = restored(target, later)
    assert back.status is FactStatus.ACTIVE
    assert (back.invalid_at, back.expired_at, back.last_confirmed_at) == (None, None, later)
    assert (
        replace(
            back,
            status=target.status,
            invalid_at=target.invalid_at,
            expired_at=target.expired_at,
            last_confirmed_at=T0,
        )
        == target
    )
