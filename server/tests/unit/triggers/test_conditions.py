"""The condition catalog of 22.1, windows, idempotency keys, lateness and target readability."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from niadra.domain.common.ids import uuid7
from niadra.domain.records import AgentAction, BusinessObject, ObjectKey, Trait
from niadra.domain.state.typed import TimerMark, Typed
from niadra.domain.traits.evidence import EvidenceRef
from niadra.domain.triggers.conditions import (
    CONDITIONS,
    RepetitionParams,
    SubjectState,
    TriggerRule,
    idempotency_key,
    is_late,
    repetition_run,
    silence_episode,
    silent_sources,
    unreadable_reason,
    window_key,
)
from niadra.domain.triggers.firing import Firing
from niadra.domain.vocabulary import ActionStatus, Audience, HandleStatus
from niadra.testing.cell import BILLING_SOURCE
from tests.unit.objects.fakes import episode, handle, open_item

NOW = datetime(2026, 9, 22, 18, 0, tzinfo=UTC)
H = handle()


def state(**kw: Any) -> SubjectState:
    return SubjectState(now=NOW, handle_ids=frozenset({H.handle_id}), **kw)


def hours(n: float) -> datetime:
    return NOW + timedelta(hours=n)


def test_promise_overdue_after_the_grace_days() -> None:
    item = open_item(H, hours(-30))
    assert CONDITIONS["promise.overdue"](state(open_items=[item]), {}).became_true_at == hours(-30)
    assert CONDITIONS["promise.overdue"](state(open_items=[item]), {"days": 2}) is None
    customer = open_item(H, hours(-30), owner="customer")
    assert CONDITIONS["promise.overdue"](state(open_items=[customer]), {}) is None


def test_open_item_due_within_the_lead() -> None:
    item = open_item(H, hours(5))
    match = CONDITIONS["open_item.due"](state(open_items=[item]), {"lead_hours": 6})
    assert match
    assert match.became_true_at == hours(-1)
    assert CONDITIONS["open_item.due"](state(open_items=[item]), {"lead_hours": 2}) is None


def test_trait_present_by_name_and_value() -> None:
    trait = Trait(
        uuid7(),
        uuid7(),
        1,
        "recurring_complaint",
        "technical_visit",
        "volatile",
        "v3",
        hours(-2),
        hours(-3),
        hours(2000),
        0.9,
    )
    refs = (EvidenceRef("episode", uuid7(), H.handle_id),)
    s = state(traits=[trait], trait_evidence={trait.trait_id: refs})
    match = CONDITIONS["trait.present"](s, {"name": "recurring_complaint"})
    assert match
    assert [e.kind for e in match.evidence] == ["trait", "episode"]
    assert match.became_true_at == hours(-2)
    assert CONDITIONS["trait.present"](s, {"name": "recurring_complaint", "value": "invoice"}) is None


def test_sentiment_drop_against_the_previous_sessions() -> None:
    eps = [episode(H, hours(-i * 24), sentiment=0.5) for i in (2, 3, 4)] + [
        episode(H, hours(-1), sentiment=-0.4)
    ]
    assert CONDITIONS["sentiment.drop"](state(episodes=eps), {"drop": 0.5})
    assert CONDITIONS["sentiment.drop"](state(episodes=eps), {"drop": 1.0}) is None


def test_identity_ambiguous_and_divergent_actions() -> None:
    ambiguous = handle(status=HandleStatus.AMBIGUOUS)
    assert CONDITIONS["identity.ambiguous"](state(handles=[H, ambiguous]), {})
    assert CONDITIONS["identity.ambiguous"](state(handles=[H]), {}) is None
    action = AgentAction(
        uuid7(), uuid7(), BILLING_SOURCE, "credit", ActionStatus.DIVERGENT, hours(-10), uuid7()
    )
    match = CONDITIONS["fact.contradicted_by_system_event"](
        state(divergent_actions=[(action, H.handle_id)]), {}
    )
    assert match
    assert match.became_true_at == hours(-4)  # the six-hour window ran out
    old = replace(action, occurred_at=hours(-24 * 8))
    assert (
        CONDITIONS["fact.contradicted_by_system_event"](state(divergent_actions=[(old, H.handle_id)]), {})
        is None
    )


def test_recontact_within_72_hours_on_the_same_category() -> None:
    first = episode(H, hours(-50))
    again = episode(H, hours(-1))
    assert CONDITIONS["recontact"](state(episodes=[first, again]), {})
    other_topic = episode(H, hours(-1), category="invoice")
    assert CONDITIONS["recontact"](state(episodes=[first, other_topic]), {}) is None
    assert CONDITIONS["recontact"](state(episodes=[first, other_topic]), {"same_category": False})
    assert CONDITIONS["recontact"](state(episodes=[episode(H, hours(-100)), again]), {}) is None


def test_a_timer_of_the_subjects_object_fired_within_the_window() -> None:
    firing = TimerMark(uuid7(), "fired", hours(-2), fired_at=hours(-1))
    typed = Typed(timers={"notice_48h": firing, "other": TimerMark(uuid7(), "armed", hours(5))})
    notice = BusinessObject(
        uuid7(), ObjectKey("notice", "court", "n-1"), {}, hours(-3), BILLING_SOURCE, H.handle_id, typed=typed
    )
    found = CONDITIONS["object.timer_due"](state(objects=[notice]), {"type": "notice", "timer": "notice_48h"})
    assert found is not None
    assert found.became_true_at == hours(-1)
    assert [(e.kind, e.id) for e in found.evidence] == [("object", notice.object_id)]
    assert found.episode == str(firing.timer_id)
    assert CONDITIONS["object.timer_due"](state(objects=[notice]), {"timer": "other"}) is None
    assert CONDITIONS["object.timer_due"](state(objects=[notice]), {"within_hours": 0.5}) is None
    assert CONDITIONS["object.timer_due"](state(objects=[notice]), {"type": "order"}) is None


def test_silent_sources() -> None:
    a, b = uuid7(), uuid7()
    assert silent_sources({a: hours(-2), b: hours(-0.1)}, 60, NOW) == [a]


def test_one_window_per_rule_and_subject() -> None:
    assert window_key(NOW, 24) == window_key(NOW.replace(hour=1), 24)
    assert window_key(NOW, 24) != window_key(NOW + timedelta(days=1), 24)
    assert window_key(NOW, 1) != window_key(NOW + timedelta(hours=1), 1)


def test_idempotency_key_is_order_free_and_changes_with_evidence_or_version() -> None:
    e1, e2 = EvidenceRef("episode", uuid7(), H.handle_id), EvidenceRef("open_item", uuid7(), H.handle_id)
    assert idempotency_key("r", "v1", [e1, e2]) == idempotency_key("r", "v1", [e2, e1])
    assert idempotency_key("r", "v1", [e1]) != idempotency_key("r", "v1", [e1, e2])
    assert idempotency_key("r", "v1", [e1]) != idempotency_key("r", "v2", [e1])


def test_lateness_default_and_per_rule() -> None:
    assert not is_late(hours(-1), NOW, {})
    assert is_late(hours(-3), NOW, {})
    assert is_late(hours(-1), NOW, {"late_after_minutes": 30})


def test_firing_separated_by_an_unmerge() -> None:
    other = uuid7()
    firing = Firing(
        uuid7(), "r", "v1", uuid7(), "24h:1", b"k", (EvidenceRef("episode", uuid7(), other),), 1, NOW
    )
    assert firing.separated_from(frozenset({H.handle_id}))
    assert not firing.separated_from(frozenset({H.handle_id, other}))


def _reason(condition: str, audience: Audience | None, **kw: Any) -> str | None:
    rule = TriggerRule("r", condition, "v1", {"name": "overdue_invoices"}, "crm")
    base: dict[str, Any] = {
        "endpoint_found": True,
        "endpoint_enabled": True,
        "endpoint_types": ["*"],
        "audience": audience,
        "source_purposes": frozenset({"marketing"}),
        "trait_purposes": frozenset(),
    }
    return unreadable_reason(rule, **{**base, **kw})


def test_the_target_must_be_able_to_read_the_payload() -> None:
    assert _reason("promise.overdue", Audience.INTERNAL_AGENT) is None
    assert (
        _reason("trait.present", Audience.INTERNAL_AGENT, trait_purposes=frozenset({"billing"}))
        == "purpose_not_allowed"
    )
    assert (
        _reason(
            "trait.present", Audience.INTERNAL_AGENT, trait_audiences=frozenset({Audience.CUSTOMER_AGENT})
        )
        == "audience_not_allowed"
    )
    assert _reason("identity.ambiguous", Audience.CUSTOMER_AGENT) == "audience_cannot_read_identity"
    assert _reason("promise.overdue", Audience.ANALYST) == "audience_cannot_receive_triggers"
    assert (
        _reason("promise.overdue", Audience.HUMAN, endpoint_types=["memory.updated"])
        == "endpoint_not_subscribed"
    )
    assert _reason("promise.overdue", Audience.HUMAN, endpoint_enabled=False) == "endpoint_disabled"
    assert _reason("promise.overdue", None) == "endpoint_unknown"


def test_a_second_silence_of_the_same_source_is_a_new_occurrence() -> None:
    # The key used to be the source alone, so the second drop never warned for 400 days.
    source = EvidenceRef("source", BILLING_SOURCE, BILLING_SOURCE)
    first = idempotency_key("silent", "v1", [source], silence_episode(hours(-30)))
    again = idempotency_key("silent", "v1", [source], silence_episode(hours(-2)))
    assert first != again
    assert first == idempotency_key("silent", "v1", [source], silence_episode(hours(-30)))
    # Conditions without an occurrence keep the key they always had.
    assert idempotency_key("silent", "v1", [source]) == idempotency_key("silent", "v1", [source], "")


DAY = date(2026, 9, 22)


def days(*counts: tuple[int, int]) -> dict[date, tuple[int, int]]:
    """(questions, repeated) per day, the last one being DAY."""
    return {DAY - timedelta(days=len(counts) - 1 - n): c for n, c in enumerate(counts)}


def test_the_repetition_run_starts_on_the_first_day_above_and_needs_enough_questions() -> None:
    params = RepetitionParams.of({"above": 0.2, "window_days": 2, "min_questions": 10})
    # Two-day windows: 1 of 10, 2 of 20, 8 of 20, 12 of 20 asked again -> above on the last two days.
    history = days((10, 1), (10, 1), (10, 7), (10, 5))
    assert repetition_run(history, DAY, params) == DAY - timedelta(days=1)
    assert repetition_run(days((10, 1), (10, 1)), DAY, params) is None
    # 3 of 6 is half, but six questions say nothing.
    assert repetition_run(days((2, 1), (4, 2)), DAY, params) is None
    assert repetition_run(days((2, 1), (4, 2)), DAY, replace(params, min_questions=6)) == DAY


def test_repetition_rule_parameters_have_defaults_and_bounds() -> None:
    assert RepetitionParams.of({}) == RepetitionParams(0.2, 7, 30)
    assert RepetitionParams.of({"source_id": str(BILLING_SOURCE)}).source_id == BILLING_SOURCE
    for wrong in (
        {"above": 1.5},
        {"window_days": 0},
        {"min_questions": 0},
        {"above": "x"},
        {"source_id": "y"},
    ):
        with pytest.raises(ValueError, match="unusable repetition parameters"):
            RepetitionParams.of(wrong)
    assert CONDITIONS["context_use.repetition_rate"](state(), {}) is None  # the sweep evaluates it
