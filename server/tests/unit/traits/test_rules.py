"""The eight catalog rules of 20.1, the sensitive barrier and the single-evidence exception (20.4)."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from niadra.domain.common.errors import InvalidInput
from niadra.domain.records import Episode, EventRecord, HandleRecord, OpenItem
from niadra.domain.serve.recurrence import RECURRENCE_INTENT, RECURRENCE_WINDOW_DAYS, recurrence
from niadra.domain.traits.barrier import Barrier
from niadra.domain.traits.rules import (
    CATALOG,
    MAX_ACTIVE_TRAITS,
    Layer,
    TraitInputs,
    TraitRule,
    derive,
    event_window,
    released_to,
    validate_rule,
    within_hot_window,
)
from niadra.domain.vocabulary import Audience, EventKind, HandleType, SubjectKind
from tests.unit.objects.fakes import episode, event, handle, open_item

NOW = datetime(2026, 9, 22, 18, 0, tzinfo=UTC)
MARINA = handle()
OPEN = Barrier.build([], None)


def days_ago(n: float) -> datetime:
    return NOW - timedelta(days=n)


def rule(catalog: str, rule_id: str | None = None, **params: Any) -> TraitRule:
    return TraitRule(rule_id or catalog, catalog, "v1", params)


def inputs(
    episodes: Sequence[Episode] = (),
    items: Sequence[OpenItem] = (),
    events: Sequence[EventRecord] = (),
    kind: SubjectKind = SubjectKind.PERSON,
    handles: Sequence[HandleRecord] = (MARINA,),
) -> TraitInputs:
    return TraitInputs(
        NOW, kind, frozenset(h.handle_id for h in handles), list(episodes), list(items), list(events)
    )


def only(found: list[Any]) -> Any:
    assert len(found) == 1, found
    return found[0]


def test_recurring_complaint_matches_the_8_7_example_and_expires_on_2_november() -> None:
    dates = [
        datetime(2026, 8, 4, 10, tzinfo=UTC),
        datetime(2026, 8, 29, 10, tzinfo=UTC),
        NOW - timedelta(hours=1),
    ]
    trait = only(derive([rule("recurring_complaint")], inputs([episode(MARINA, d) for d in dates]), OPEN))
    assert trait.value == "technical_visit"
    assert trait.layer is Layer.VOLATILE
    assert len(trait.evidence) == 3
    assert trait.expires_at == datetime(2026, 11, 2, 10, tzinfo=UTC)


def test_recurring_complaint_needs_three_complaints_of_the_same_category() -> None:
    eps = [
        episode(MARINA, days_ago(1)),
        episode(MARINA, days_ago(2)),
        episode(MARINA, days_ago(3), category="invoice"),
        episode(MARINA, days_ago(4), intent="question"),
    ]
    assert derive([rule("recurring_complaint")], inputs(eps), OPEN) == []


def test_the_pattern_and_the_history_count_the_same_complaints_in_the_same_window() -> None:
    # One window and one filter, so "how many times" and the pattern never disagree.
    assert CATALOG["recurring_complaint"].defaults["window_days"] == RECURRENCE_WINDOW_DAYS == 90
    assert CATALOG["recurring_complaint"].defaults["intent"] == RECURRENCE_INTENT
    eps = [
        episode(MARINA, days_ago(1)),
        episode(MARINA, days_ago(30)),
        episode(MARINA, days_ago(60)),
        episode(MARINA, days_ago(5), intent="question"),  # asked, never complained: not a recurrence
        episode(MARINA, days_ago(120)),  # outside the window
    ]
    history = recurrence(eps, "technical_visit", now=NOW)
    assert history is not None
    trait = only(derive([rule("recurring_complaint")], inputs(eps), OPEN))
    assert history.occurrences == len(trait.evidence) == 3


def test_a_pattern_is_never_one_episode_even_if_the_tenant_asks() -> None:
    assert (
        derive([rule("recurring_complaint", min_count=1)], inputs([episode(MARINA, days_ago(1))]), OPEN) == []
    )
    two = [episode(MARINA, days_ago(1)), episode(MARINA, days_ago(2))]
    assert (
        only(derive([rule("recurring_complaint", min_count=1)], inputs(two), OPEN)).value == "technical_visit"
    )


def test_asked_to_cancel_is_a_single_evidence_trait_and_stable() -> None:
    trait = only(
        derive(
            [rule("asked_to_cancel")], inputs([episode(MARINA, days_ago(50), intent="cancellation")]), OPEN
        )
    )
    assert trait.layer is Layer.STABLE
    assert trait.expires_at == days_ago(50) + timedelta(days=180)
    assert (
        derive(
            [rule("asked_to_cancel")], inputs([episode(MARINA, days_ago(181), intent="cancellation")]), OPEN
        )
        == []
    )


def test_broken_company_promise_from_one_overdue_unclosed_item() -> None:
    due = days_ago(12)
    trait = only(derive([rule("broken_company_promise")], inputs(items=[open_item(MARINA, due)]), OPEN))
    assert trait.value == "1"
    assert trait.evidence[0].kind == "open_item"
    assert trait.expires_at == due + timedelta(days=60)
    customer_owes = open_item(MARINA, due, owner="customer")
    not_due = open_item(MARINA, NOW + timedelta(days=1))
    assert derive([rule("broken_company_promise")], inputs(items=[customer_owes, not_due]), OPEN) == []


def test_preferred_channel_needs_share_and_volume() -> None:
    eps = [episode(MARINA, days_ago(i), channel="whatsapp") for i in range(1, 5)] + [
        episode(MARINA, days_ago(6), channel="voice")
    ]
    trait = only(derive([rule("preferred_channel")], inputs(eps), OPEN))
    assert trait.value == "whatsapp"
    assert trait.layer is Layer.STABLE
    assert derive([rule("preferred_channel")], inputs(eps[:4]), OPEN) == []  # fewer than 5 conversations
    split = eps[:3] + [episode(MARINA, days_ago(i), channel="voice") for i in (7, 8)]
    assert derive([rule("preferred_channel")], inputs(split), OPEN) == []  # 60% < 70%


def test_does_not_answer_calls_needs_unanswered_calls_and_a_text_reply() -> None:
    calls = [
        event(EventKind.SYSTEM_EVENT, days_ago(i), [MARINA], canonical_type="call.unanswered")
        for i in (1, 2, 3)
    ]
    reply = episode(MARINA, days_ago(1), channel="whatsapp")
    trait = only(derive([rule("does_not_answer_calls")], inputs([reply], events=calls), OPEN))
    assert trait.value == "3"
    assert {e.kind for e in trait.evidence} == {"event", "episode"}
    assert derive([rule("does_not_answer_calls")], inputs([], events=calls), OPEN) == []


def test_escalated_to_human_counts_escalations() -> None:
    eps = [episode(MARINA, days_ago(i), outcome="escalated") for i in (5, 40)]
    assert only(derive([rule("escalated_to_human")], inputs(eps), OPEN)).value == "2"
    assert derive([rule("escalated_to_human")], inputs(eps[:1]), OPEN) == []


def test_declining_sentiment_needs_the_last_three_sessions_below_threshold() -> None:
    low = [episode(MARINA, days_ago(i), sentiment=-0.6) for i in (1, 2, 3)]
    assert only(derive([rule("declining_sentiment")], inputs(low), OPEN)).evidence
    mixed = [*low[:2], episode(MARINA, days_ago(0.5), sentiment=0.4)]
    assert derive([rule("declining_sentiment")], inputs(mixed), OPEN) == []


def test_overdue_invoices_counts_system_events() -> None:
    # 90 days by default: the events a rule counts stay that long in the live database.
    overdue = [
        event(EventKind.SYSTEM_EVENT, days_ago(i), [MARINA], canonical_type="invoice.overdue")
        for i in (10, 80, 100)
    ]
    trait = only(derive([rule("overdue_invoices")], inputs(events=overdue), OPEN))
    assert trait.value == "2"
    assert trait.expires_at == days_ago(80) + timedelta(days=90)


def test_a_rule_over_events_never_reaches_past_the_hot_window() -> None:
    # A stored rule asking for 180 days derives over the 90 the events stay.
    wide = rule("overdue_invoices", window_days=180)
    assert event_window(wide, 90) == 180
    assert event_window(rule("overdue_invoices"), 90) is None
    assert event_window(rule("recurring_complaint", window_days=180), 90) is None  # episodes stay
    cut = within_hot_window(wide, 60)
    assert cut.params["window_days"] == 60
    overdue = [
        event(EventKind.SYSTEM_EVENT, days_ago(i), [MARINA], canonical_type="invoice.overdue")
        for i in (10, 50, 70)
    ]
    trait = only(derive([cut], inputs(events=overdue), OPEN))
    assert trait.value == "2"


def test_sensitive_episodes_never_feed_any_rule() -> None:
    barrier = Barrier.build(["health_plan"], None)
    eps = [episode(MARINA, days_ago(i), category="health") for i in (1, 2, 3)]
    assert derive([rule("recurring_complaint")], inputs(eps), barrier) == []
    tenant_sensitive = [episode(MARINA, days_ago(i), category="health_plan") for i in (1, 2, 3)]
    assert derive([rule("recurring_complaint")], inputs(tenant_sensitive), barrier) == []


def test_rule_parameters_pass_the_allow_list_and_never_name_a_sensitive_category() -> None:
    allow = Barrier.build([], ["technical_visit"])
    with pytest.raises(InvalidInput):
        validate_rule(rule("recurring_complaint", category="health"), OPEN)
    with pytest.raises(InvalidInput):
        validate_rule(rule("recurring_complaint", category="invoice"), allow)
    with pytest.raises(InvalidInput):
        validate_rule(rule("overdue_invoices", event_type="health.exam_denied"), OPEN)
    with pytest.raises(InvalidInput):
        validate_rule(rule("recurring_complaint", free_text="anything"), OPEN)
    with pytest.raises(InvalidInput):
        validate_rule(rule("recurring_complaint", window_days=400), OPEN)
    validate_rule(rule("recurring_complaint", category="technical_visit"), allow)


@pytest.mark.parametrize(
    "name",
    [
        "politica",
        "opiniao_politica",
        "sindicato",
        "filiacao_sindical",
        "vida_sexual",
        "orientacao_sexual",
        "origem_etnica",
        "religião",
        "salud",
        "orientacion_sexual",
        "autorizacao_de_exame",
        "exam_authorization",
        "resultado_do_exame.atrasado",
        "cirurgia_remarcada",
        "dizimo",
    ],
)
def test_a_pattern_never_names_a_sensitive_category_in_portuguese_or_spanish(name: str) -> None:
    # The law's categories under their Portuguese and Spanish names, and shortcuts that carry
    # them ("exam authorization"), are refused as parameters and never feed a rule.
    with pytest.raises(InvalidInput):
        validate_rule(rule("recurring_complaint", category=name), OPEN)
    eps = [episode(MARINA, days_ago(i), category=name) for i in (1, 2, 3)]
    assert derive([rule("recurring_complaint")], inputs(eps), OPEN) == []


@pytest.mark.parametrize("name", ["politica_de_troca", "technical_visit", "raca", "entrega_atrasada"])
def test_ordinary_categories_with_similar_words_still_feed_rules(name: str) -> None:
    validate_rule(rule("recurring_complaint", category=name), OPEN)


def test_the_allow_list_filters_trait_values_and_rules_it_no_longer_admits() -> None:
    eps = [episode(MARINA, days_ago(i), category="invoice") for i in (1, 2, 3)]
    assert derive([rule("recurring_complaint")], inputs(eps), Barrier.build([], ["technical_visit"])) == []
    stale_rule = rule("recurring_complaint", category="invoice")
    assert derive([stale_rule], inputs(eps), Barrier.build([], ["technical_visit"])) == []


def test_account_traits_need_two_different_contacts() -> None:
    driver, buyer = handle(), handle()
    account = handle(HandleType.ORG_REGISTRY_HMAC, SubjectKind.ACCOUNT)
    one_contact = [episode(driver, days_ago(i)) for i in (1, 2, 3)]
    two_contacts = [episode(driver, days_ago(1)), episode(driver, days_ago(2)), episode(buyer, days_ago(3))]
    everyone = (driver, buyer, account)
    assert (
        derive(
            [rule("recurring_complaint")],
            inputs(one_contact, kind=SubjectKind.ACCOUNT, handles=everyone),
            OPEN,
        )
        == []
    )
    assert derive(
        [rule("recurring_complaint")], inputs(two_contacts, kind=SubjectKind.ACCOUNT, handles=everyone), OPEN
    )


def test_active_traits_are_capped_per_profile() -> None:
    eps = [
        episode(MARINA, days_ago(1), outcome="escalated"),
        episode(MARINA, days_ago(2), outcome="escalated"),
    ]
    rules = [rule("escalated_to_human", f"escalated_{i}") for i in range(MAX_ACTIVE_TRAITS + 5)]
    assert len(derive(rules, inputs(eps), OPEN)) == MAX_ACTIVE_TRAITS


def test_the_catalog_releases_overdue_invoices_to_billing_and_sentiment_to_customer_agents() -> None:
    # Without a purpose in the tenant's rule, the catalog's release applies.
    assert released_to("overdue_invoices", frozenset()) == (frozenset({"billing"}), frozenset())
    assert released_to("overdue_invoices", frozenset({"collections"})) == (
        frozenset({"collections"}),
        frozenset(),
    )
    assert released_to("declining_sentiment", frozenset()) == (
        frozenset(),
        frozenset({Audience.CUSTOMER_AGENT}),
    )
    assert released_to("recurring_complaint", frozenset()) == (frozenset(), frozenset())


def test_without_an_allow_list_only_the_declared_categories_become_patterns() -> None:
    # An undeclared category, a typo or a topic the word lists miss, never becomes a pattern by
    # default; an explicit list still decides alone, and event types are not categories.
    declared = Barrier.build([], None, ["technical_visit", "billing"])
    visits = [episode(MARINA, days_ago(i), category="technical_visit") for i in (1, 2, 3)]
    oncology = [episode(MARINA, days_ago(i), category="oncologia") for i in (1, 2, 3)]
    assert only(derive([rule("recurring_complaint")], inputs(visits), declared)).value == "technical_visit"
    assert derive([rule("recurring_complaint")], inputs(oncology), declared) == []
    with pytest.raises(InvalidInput):
        validate_rule(rule("recurring_complaint", category="oncologia"), declared)
    validate_rule(rule("overdue_invoices", event_type="invoice.overdue"), declared)
    explicit = Barrier.build([], ["oncologia"], ["technical_visit"])
    assert only(derive([rule("recurring_complaint")], inputs(oncology), explicit)).value == "oncologia"
    assert only(derive([rule("recurring_complaint")], inputs(oncology), OPEN)).value == "oncologia"


def test_the_space_declares_its_default_allow_list() -> None:
    from niadra.app.traits import barrier_for
    from niadra.testing.cell import sample_config

    barrier = barrier_for(sample_config())
    assert barrier.declared is not None
    assert {"billing", "technical_visit", "contract"} <= barrier.declared
    assert not barrier.permits_category("health")
