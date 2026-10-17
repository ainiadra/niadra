"""The four signals of 19.2 and every "does not count" rule, on hand-built manifests and turns."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import QuestionType
from niadra.domain.measure.model import (
    AgentClaim,
    AgentQuestion,
    ContextStamp,
    Delivery,
    Handoff,
    Manifest,
    ManifestItem,
    SessionAction,
    TransferState,
)
from niadra.domain.measure.normalize import normalize_value
from niadra.domain.measure.signals import (
    CONTACT_REASON,
    MeasureRules,
    SessionEvidence,
    context_entered_at,
    measure_deliveries,
    transfer_state,
)

T0 = datetime(2026, 9, 22, 14, 7, tzinfo=UTC)
VOICE = UUID("0192f5a0-0000-7000-8000-00000000c002")
HUMAN_DESK = UUID("0192f5a0-0000-7000-8000-00000000c009")

NAME = ManifestItem("f-name", "fact", predicate="preferred_name", category="contact", value="Marina")
VISIT = ManifestItem("oi-visit", "open_item", predicate="technical_visit", object_ref="visit/field/77")
CREDIT = ManifestItem("act-credit", "action", predicate="invoice_credit", object_id="obj-invoice")
DOC = ManifestItem("f-doc", "fact", predicate="document", category="identity")
MANIFEST = Manifest("m1", "voice", items=(NAME, VISIT, CREDIT), withheld=(DOC,))
DELIVERY = Delivery("m1", VOICE, T0)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def ask(
    kind: QuestionType,
    predicate: str | None,
    minutes: float = 1,
    source: UUID = VOICE,
    object_ref: str | None = None,
) -> AgentQuestion:
    return AgentQuestion(kind, uuid7(), source, at(minutes), predicate=predicate, object_ref=object_ref)


def claim(
    predicate: str | None,
    value: str | None = None,
    minutes: float = 1,
    contradicts: tuple[str, ...] = (),
    object_ref: str | None = None,
) -> AgentClaim:
    return AgentClaim(
        uuid7(),
        VOICE,
        at(minutes),
        predicate=predicate,
        value=value,
        object_ref=object_ref,
        contradicts=contradicts,
    )


def run(
    evidence: SessionEvidence,
    rules: MeasureRules | None = None,
    manifest: Manifest = MANIFEST,
    delivery: Delivery = DELIVERY,
):
    rules = rules or MeasureRules(single_valued=frozenset({"preferred_name"}))
    [outcome] = measure_deliveries([(delivery, manifest)], evidence, rules)
    return outcome, {i.item_id: i for i in outcome.items}


class TestRepetition:
    def test_asking_again_for_a_delivered_item_is_repetition(self):
        outcome, items = run(SessionEvidence(questions=(ask(QuestionType.ASKS_DATA, "preferred_name"),)))
        assert (outcome.questions, outcome.repeated) == (1, 1)
        assert items["f-name"].repeated

    def test_a_question_before_the_delivery_is_counted_but_never_repetition(self):
        outcome, _ = run(
            SessionEvidence(questions=(ask(QuestionType.ASKS_DATA, "preferred_name", minutes=-1),))
        )
        assert (outcome.questions, outcome.repeated) == (1, 0)

    def test_verification_required_by_policy_is_not_repetition(self):
        outcome, _ = run(SessionEvidence(questions=(ask(QuestionType.VERIFIES, "document"),)))
        assert (outcome.questions, outcome.repeated) == (0, 0)

    def test_asking_about_a_withheld_item_is_not_repetition(self):
        outcome, items = run(SessionEvidence(questions=(ask(QuestionType.ASKS_DATA, "document"),)))
        assert (outcome.questions, outcome.repeated) == (1, 0)
        assert "f-doc" not in items

    def test_confirmation_counts_unless_the_source_confirms_before_acting(self):
        evidence = SessionEvidence(questions=(ask(QuestionType.CONFIRMS, None, object_ref="visit/field/77"),))
        counted, _ = run(evidence)
        allowed, _ = run(evidence, MeasureRules(confirms_before_acting=frozenset({VOICE})))
        assert counted.repeated == 1
        assert allowed.repeated == 0

    def test_ambiguous_questions_stay_unclassified(self):
        outcome, _ = run(SessionEvidence(questions=(ask(QuestionType.AMBIGUOUS, "preferred_name"),)))
        assert (outcome.questions, outcome.repeated) == (0, 0)

    def test_what_happened_matches_the_delivered_episodes_and_open_items(self):
        outcome, items = run(SessionEvidence(questions=(ask(QuestionType.ASKS_DATA, CONTACT_REASON),)))
        assert outcome.repeated == 1
        assert items["oi-visit"].repeated
        assert not items["f-name"].repeated

    def test_a_task_session_does_not_measure_repetition(self):
        evidence = SessionEvidence(questions=(ask(QuestionType.ASKS_DATA, "preferred_name"),))
        outcome, _ = run(evidence, MeasureRules(task_session=True))
        assert (outcome.questions, outcome.repeated) == (0, 0)

    def test_a_holdout_manifest_measures_what_the_control_group_asked(self):
        holdout = Delivery("m1", VOICE, T0, holdout=True, experiment_group="control")
        evidence = SessionEvidence(
            questions=(ask(QuestionType.ASKS_DATA, "preferred_name"),),
            claims=(claim("preferred_name", "Marina"),),
        )
        outcome, items = run(evidence, delivery=holdout)
        assert outcome.repeated == 1
        assert not items["f-name"].used

    def test_another_source_question_does_not_count_against_this_delivery(self):
        other = UUID("0192f5a0-0000-7000-8000-00000000c001")
        outcome, _ = run(
            SessionEvidence(questions=(ask(QuestionType.ASKS_DATA, "preferred_name", source=other),))
        )
        assert outcome.questions == 0


class TestUse:
    def test_a_pertinent_claim_uses_the_item(self):
        outcome, items = run(SessionEvidence(claims=(claim("technical_visit", "remarcada"),)))
        assert outcome.used == 1
        assert items["oi-visit"].used

    def test_a_claim_without_predicate_or_object_uses_nothing(self):
        outcome, _ = run(SessionEvidence(claims=(claim(None, "vou olhar tudo o que temos aqui"),)))
        assert outcome.used == 0

    def test_a_claim_before_the_delivery_uses_nothing(self):
        outcome, _ = run(SessionEvidence(claims=(claim("technical_visit", minutes=-2),)))
        assert outcome.used == 0

    def test_an_action_on_the_item_object_or_closing_it_uses_the_item(self):
        actions = (
            SessionAction(uuid7(), VOICE, at(2), object_id="obj-invoice"),
            SessionAction(uuid7(), VOICE, at(3), closes_item_id="oi-visit"),
        )
        outcome, items = run(SessionEvidence(actions=actions))
        assert outcome.used == 2
        assert items["act-credit"].used
        assert items["oi-visit"].used

    def test_an_unused_item_is_not_penalised(self):
        outcome, items = run(SessionEvidence())
        assert (outcome.used, outcome.repeated, outcome.contradicted) == (0, 0, 0)
        assert not any(i.used for i in items.values())


class TestContradiction:
    def test_a_different_value_for_a_single_valued_delivered_fact(self):
        outcome, items = run(SessionEvidence(claims=(claim("preferred_name", "Mariana"),)))
        assert outcome.contradicted == 1
        assert items["f-name"].contradicted
        assert not items["f-name"].used

    def test_the_same_value_normalized_is_use_not_contradiction(self):
        outcome, items = run(SessionEvidence(claims=(claim("preferred_name", " MARINA "),)))
        assert outcome.contradicted == 0
        assert items["f-name"].used

    def test_the_extractor_links_multi_valued_contradictions_by_item_id(self):
        outcome, items = run(
            SessionEvidence(claims=(claim("invoice_credit", "em análise", contradicts=("act-credit",)),))
        )
        assert outcome.contradicted == 1
        assert items["act-credit"].contradicted

    def test_contradicting_a_withheld_item_is_a_selection_miss_not_the_agent(self):
        outcome, items = run(SessionEvidence(claims=(claim("document", "123", contradicts=("f-doc",)),)))
        assert outcome.contradicted == 0
        assert items["f-doc"].to_json()["selection_miss"] is True

    def test_holdout_deliveries_never_count_contradictions(self):
        holdout = Delivery("m1", VOICE, T0, holdout=True)
        outcome, _ = run(SessionEvidence(claims=(claim("preferred_name", "Mariana"),)), delivery=holdout)
        assert outcome.contradicted == 0


class TestAttributionAndCoverage:
    def test_each_turn_belongs_to_the_latest_earlier_delivery_of_its_source(self):
        later = Delivery("m2", VOICE, at(5))
        manifest2 = Manifest("m2", "voice", items=(NAME,))
        evidence = SessionEvidence(questions=(ask(QuestionType.ASKS_DATA, "preferred_name", minutes=6),))
        first, second = measure_deliveries(
            [(DELIVERY, MANIFEST), (later, manifest2)], evidence, MeasureRules()
        )
        assert (first.repeated, second.repeated) == (0, 1)

    def test_context_delivered_after_the_first_agent_turn_is_late(self):
        outcome, _ = run(SessionEvidence(first_agent_turn_at={VOICE: at(-1)}))
        assert outcome.late
        on_time, _ = run(SessionEvidence(first_agent_turn_at={VOICE: at(1)}))
        assert not on_time.late

    def test_the_sdk_stamp_says_when_the_context_entered_the_prompt(self):
        # Read at T0, injected only at minute 3: the agent's first answer at minute 1 went without it.
        read_early = Delivery("m1", VOICE, T0, etag="e1")
        stamped = SessionEvidence(
            first_agent_turn_at={VOICE: at(1)}, stamps=(ContextStamp(VOICE, at(3), "e1"),)
        )
        outcome, _ = run(stamped, delivery=read_early)
        assert outcome.late
        # Injected before the receipt reached the writer (a cached pack): on time, whatever the receipt says.
        cached = SessionEvidence(first_agent_turn_at={VOICE: at(-1)}, stamps=(ContextStamp(VOICE, at(-2)),))
        outcome, _ = run(cached, delivery=read_early)
        assert not outcome.late

    def test_stamps_of_another_pack_or_source_fall_back_to_the_receipt(self):
        delivery = Delivery("m1", VOICE, T0, etag="e1")
        other = (ContextStamp(VOICE, at(-5), "e2"), ContextStamp(HUMAN_DESK, at(-5), "e1"))
        assert context_entered_at(delivery, other) == T0
        outcome, _ = run(
            SessionEvidence(first_agent_turn_at={VOICE: at(-1)}, stamps=other), delivery=delivery
        )
        assert outcome.late


class TestTransfer:
    integrated = frozenset({VOICE, HUMAN_DESK})

    def test_no_handoff(self):
        assert transfer_state((), (), self.integrated) is TransferState.NONE

    def test_the_target_read_within_the_window(self):
        handoff = Handoff(at(10), VOICE, HUMAN_DESK)
        assert transfer_state([handoff], [(HUMAN_DESK, at(12))], self.integrated) is TransferState.READ

    def test_the_target_read_too_late_or_not_at_all(self):
        handoff = Handoff(at(10), VOICE, HUMAN_DESK)
        assert transfer_state([handoff], [(HUMAN_DESK, at(25))], self.integrated) is TransferState.UNREAD
        assert transfer_state([handoff], [(VOICE, at(11))], self.integrated) is TransferState.UNREAD

    def test_a_target_that_is_not_integrated_is_coverage_not_unread(self):
        unknown = Handoff(at(10), VOICE, UUID(int=5))
        blank = Handoff(at(10), VOICE, None)
        assert transfer_state([unknown, blank], [], self.integrated) is TransferState.NOT_INTEGRATED


class TestNormalize:
    def test_dates_amounts_and_ids(self):
        assert normalize_value("22/9/2026") == normalize_value("2026-09-22") == "2026-09-22"
        assert normalize_value("R$ 1.040,5") == normalize_value("1040,50") == "1040.50"
        assert normalize_value("pedido nº 4471") != "4471"
        assert normalize_value("nº 4471") == normalize_value("#4471") == "4471"
        assert normalize_value("  São   Paulo ") == "sao paulo"
