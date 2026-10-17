from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.domain.common.ids import uuid7_ms
from niadra.domain.memory.anchor import anchor
from niadra.domain.memory.ids import stable_uuid7
from niadra.domain.memory.items import (
    Closing,
    closes,
    is_overdue,
    recorded_words,
    restates_on_object,
    to_judge,
    to_judge_retelling,
)
from niadra.domain.memory.profile import merge_attr, survive
from niadra.domain.memory.summaries import tail_summaries
from niadra.domain.records import (
    AgentAction,
    Episode,
    EventRecord,
    HandleRecord,
    ObjectKey,
    OpenItem,
    ProfileAttr,
)
from niadra.domain.vocabulary import (
    ActionStatus,
    EventKind,
    HandleStatus,
    HandleType,
    OpenItemStatus,
    Speaker,
    SubjectKind,
    Verification,
)

T0 = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c003")
H1 = UUID("0192f5a0-0000-7000-8000-000000000001")
H2 = UUID("0192f5a0-0000-7000-8000-000000000002")
H3 = UUID("0192f5a0-0000-7000-8000-000000000003")
INVOICE = ObjectKey("invoice", "erp", "0823")
CASE = ObjectKey("legal_case", "cases", "case-1")


def handle(handle_id: UUID, level: Verification, type_: HandleType = HandleType.PHONE_E164) -> HandleRecord:
    return HandleRecord(handle_id, type_, "", SubjectKind.PERSON, "x", HandleStatus.ACTIVE, level, T0, T0)


def ev(*handles: UUID, kind: EventKind = EventKind.MESSAGE) -> EventRecord:
    return EventRecord(UUID(int=len(handles) + 99), kind, SOURCE, "chat", Speaker.CUSTOMER, T0, T0, handles)


def test_anchor_takes_the_most_verified_handle_then_the_smallest_id() -> None:
    handles = {
        H1: handle(H1, Verification.V1),
        H2: handle(H2, Verification.V3),
        H3: handle(H3, Verification.V3),
    }
    placed = anchor([ev(H1, H2, H3)], handles, [H1, H2, H3])
    assert placed.origin_handle_id == H2
    assert placed.mention_handle_ids == (H1, H3)


def test_system_events_anchor_to_the_system_id() -> None:
    handles = {H1: handle(H1, Verification.V3), H2: handle(H2, Verification.V0, HandleType.SYSTEM_ID)}
    assert anchor([ev(H1, H2, kind=EventKind.SYSTEM_EVENT)], handles, [H1]).origin_handle_id == H2


def test_without_evidence_handles_the_conversation_handle_is_used() -> None:
    handles = {H2: handle(H2, Verification.V1), H3: handle(H3, Verification.V1)}
    assert anchor([ev()], handles, [H3]).origin_handle_id == H3


def item(**extra: object) -> OpenItem:
    base = dict(
        item_id=UUID(int=7),
        origin_handle_id=H1,
        description="creditar",
        owner="company",
        status=OpenItemStatus.OPEN,
        created_at=T0,
        origin={"noted_at": T0.isoformat()},
        object=INVOICE,
        expected_operation="invoice.credited",
    )
    return OpenItem(**{**base, **extra})  # type: ignore[arg-type]


def closing(
    status: ActionStatus = ActionStatus.CONFIRMED,
    at: datetime = T0,
    operation: str = "credit",
    **extra: object,
) -> Closing:
    base: dict[str, object] = {"object": INVOICE}
    return Closing(operation, at, status, **{**base, **extra})  # type: ignore[arg-type]


def test_closing_by_object_and_operation_within_the_window() -> None:
    assert closes(item(), closing(at=T0 - timedelta(hours=1)))
    assert not closes(item(), closing(at=T0 + timedelta(days=4)))
    assert not closes(item(), closing(object=ObjectKey("invoice", "erp", "0999")))
    assert not closes(item(), closing(operation="refund"))


def test_the_agent_word_the_system_event_and_the_item_name_the_same_operation() -> None:
    # The item was extracted as `invoice.credited`; the billing agent records `credit` (14h06, 18.7).
    assert closes(item(expected_operation="invoice.credited"), closing(operation="credit"))
    assert closes(item(expected_operation="credit"), closing(operation="invoice.credited"))
    assert closes(item(expected_operation="Credited"), closing(operation="credit"))
    assert not closes(item(expected_operation="invoice.refunded"), closing(operation="credit"))


def test_an_item_about_an_object_closes_by_what_was_done_to_a_part_of_it() -> None:
    # The credit lands on the invoice's line, which names its invoice: the item about the invoice closes.
    line = ObjectKey("invoice_line", "erp", "0823-1")
    assert closes(item(), closing(object=line, parents=(INVOICE,)))
    assert not closes(item(), closing(object=line, parents=(ObjectKey("invoice", "erp", "0999"),)))
    assert not closes(item(), closing(object=line))
    assert not closes(item(), closing(object=line, parents=(INVOICE,), operation="refund"))


def test_declared_actions_close_only_for_trusted_sources_and_quarantine_never_does() -> None:
    assert not closes(item(), closing(ActionStatus.DECLARED))
    assert closes(item(), closing(ActionStatus.DECLARED, trusted=True))
    assert not closes(item(), closing(ActionStatus.QUARANTINED, trusted=True))


def test_closes_names_the_item_explicitly() -> None:
    assert closes(item(object=None), closing(item_id=UUID(int=7), object=None))
    assert not closes(item(), closing(item_id=UUID(int=8)))


def test_a_closing_built_from_an_action_knows_whether_the_source_is_trusted() -> None:
    action = AgentAction(UUID(int=9), UUID(int=10), SOURCE, "credit", ActionStatus.DECLARED, T0)
    assert Closing.of_action(action, INVOICE, {"credit"}).trusted
    assert Closing.of_action(action, INVOICE, {"invoice.credited"}).trusted
    assert not Closing.of_action(action, INVOICE, {"refund"}).trusted
    # A source that declared no list trusts every operation (zero touch): the gates decide.
    assert Closing.of_action(action, INVOICE, set()).trusted


def test_a_customer_facing_agents_action_closes_only_what_it_names_or_says() -> None:
    # Phase 2 of the legal integrator: an attendant's trusted `notify_client` on the case closed every item
    # waiting for `notify_client` on that case. What an attendant says it did is not a system's record: it
    # closes the item it names, or one whose every content word it says.
    told = item(
        description="Comunicar a sentença ao cliente",
        object=CASE,
        expected_operation="notify_client",
    )

    def attendant(said: str, **extra: object) -> Closing:
        base: dict[str, object] = {
            "operation": "notify_client",
            "occurred_at": T0,
            "status": ActionStatus.DECLARED,
            "object": CASE,
            "trusted": True,
            "strict": True,
            "said": said,
        }
        return Closing(**{**base, **extra})  # type: ignore[arg-type]

    assert not closes(told, attendant("Avisar cliente sobre verificação da sentença"))
    assert not closes(told, attendant("Avisar o cliente sobre a procuração"))
    assert not closes(told, attendant(""))
    assert closes(told, attendant("Comuniquei ao cliente a sentença publicada"))
    assert closes(told, attendant("nada a ver", item_id=told.item_id))
    asked = item(
        description="Pedir extratos de vendas e histórico do SAC", object=CASE, expected_operation="request"
    )
    assert not closes(asked, attendant("Pedido de envio de documentos", operation="request"))
    # The act and all but one of the other words, each by its stem: the attendant tells the subpoena without
    # saying "the client" it is talking to (juridico-sandbox, 05/10/2026), and that closes the lawyer's
    # promise; telling the deadline or another filing on the same case does not.
    warn = item(
        description="Avisará o cliente da intimação para contestar",
        object=CASE,
        expected_operation="notify_client",
    )
    assert closes(warn, attendant("Aviso da intimação para contestar, com prazo até 05/10/2026."))
    assert not closes(warn, attendant("Prazo para contestar vence em 05/10/2026."))
    assert not closes(warn, attendant("Informamos que a contestação foi protocolada no processo."))
    assert not closes(warn, attendant("Aviso da intimação para contestar", operation="deliver_document"))
    assert not closes(warn, attendant("Aviso da intimação para contestar", object=INVOICE))
    # An item of two content words is said only whole.
    short = item(description="Comunicar sentença", object=CASE, expected_operation="notify_client")
    assert not closes(short, attendant("Comuniquei ao cliente"))
    assert closes(short, attendant("Comuniquei a sentença"))
    # A system of record's confirmation, or an internal agent's trusted action, keeps closing by case and
    # operation: the strictness is only for what a customer-facing agent says.
    assert closes(told, attendant("Avisar sobre outra coisa", status=ActionStatus.CONFIRMED, strict=False))
    assert closes(told, attendant("Avisar sobre outra coisa", strict=False))


def test_what_words_cannot_settle_the_decision_model_judges_on_the_items_object() -> None:
    # juridico-sandbox, 05/10/2026: the attendant told the sentence in its own words and the lawyer's
    # "comunicar a sentença ao cliente" stayed open; "avisar o cliente sobre a verificação da sentença" says
    # more of the item's words and does not do it. The decision model judges what the gates leave open.
    told = item(
        description="Comunicar a sentença ao cliente", object=CASE, expected_operation="notify_client"
    )

    def attendant(said: str, **extra: object) -> Closing:
        base: dict[str, object] = {
            "operation": "notify_client",
            "occurred_at": T0,
            "status": ActionStatus.DECLARED,
            "object": CASE,
            "trusted": True,
            "strict": True,
            "said": said,
        }
        return Closing(**{**base, **extra})  # type: ignore[arg-type]

    synonym = attendant(
        "Informar que a sentença do processo 1013456-86.2026.8.26.0002 foi julgada improcedente"
    )
    assert to_judge(told, synonym)
    assert not closes(told, synonym)
    assert closes(told, synonym, judged=True)
    # The wrong closing of phase 2 is asked too, and only the model's "fulfils" would close it.
    checked = attendant("Avisar cliente sobre verificação da sentença")
    assert to_judge(told, checked)
    assert not closes(told, checked)
    # Said whole: no question. Nothing of the item named: no question, and a "yes" closes nothing.
    assert not to_judge(told, attendant("Comuniquei ao cliente a sentença publicada"))
    short = item(description="Comunicar sentença", object=CASE, expected_operation="notify_client")
    assert not to_judge(short, attendant("Comuniquei ao cliente"))
    assert not closes(short, attendant("Comuniquei ao cliente"), judged=True)
    # Another operation, another object, before the item was said, untrusted, or named by id: never asked.
    assert not to_judge(told, attendant(synonym.said or "", operation="deliver_document"))
    assert not to_judge(told, attendant(synonym.said or "", object=INVOICE))
    assert not to_judge(told, attendant(synonym.said or "", occurred_at=T0 - timedelta(days=30)))
    # Done late is done: a month after the promise, the model is still asked, and its "yes" closes it.
    late = attendant(synonym.said or "", occurred_at=T0 + timedelta(days=30))
    assert to_judge(told, late)
    assert closes(told, late, judged=True)
    assert not to_judge(told, attendant(synonym.said or "", trusted=False))
    assert not to_judge(told, attendant(synonym.said or "", item_id=UUID(int=99)))
    # A system of record or an internal agent closes by object and operation; nothing to judge.
    assert not to_judge(told, attendant(synonym.said or "", strict=False))


def test_an_item_that_tells_another_conversations_promise_on_the_object_is_that_promise() -> None:
    # juridico-sandbox, 05/10/2026: the attendant tells the client what the lawyer promised on the case, in
    # its own words, in every conversation; each became a second promise on the case.
    sent = item(
        description="Enviar ao cliente cópia da decisão amanhã, 06/10/2026",
        object=CASE,
        expected_operation="deliver_document",
        due_at=T0 + timedelta(days=1),
    )
    due = T0 + timedelta(days=1)
    for told in (
        "Enviar cópia da decisão ao cliente.",
        "Enviaremos a cópia da decisão em 06/10/2026.",
        "Enviar cópia da decisão amanhã, dia 06/10/2026.",
    ):
        assert restates_on_object(told, due, CASE, "deliver_document", sent), told
    assert restates_on_object("Enviar cópia da decisão.", None, CASE, "deliver_document", sent)
    # Another thing, another operation, another object, another day, or no operation: a promise of its own.
    assert not restates_on_object("Enviar comprovante de pagamento", due, CASE, "deliver_document", sent)
    assert not restates_on_object("Enviar cópia da decisão", due, CASE, "notify_client", sent)
    assert not restates_on_object("Enviar cópia da decisão", due, INVOICE, "deliver_document", sent)
    later = due + timedelta(days=3)
    assert not restates_on_object("Enviar cópia da decisão", later, CASE, "deliver_document", sent)
    assert not restates_on_object("Enviar cópia da decisão", due, CASE, None, sent)
    resolved = replace(sent, status=OpenItemStatus.RESOLVED)
    assert not restates_on_object("Enviar cópia da decisão", due, CASE, "deliver_document", resolved)


def test_two_promises_on_the_object_dated_the_same_day_one_word_apart_are_one() -> None:
    # The r5 block: "promessa: Enviar cópia da sentença ao cliente, até 06/10/2026" and "promessa: Enviar ao
    # cliente a cópia da decisão, até 06/10/2026" on one case, both deliver_document; the attendant read two.
    due = T0 + timedelta(days=1)
    sent = item(
        description="Enviar ao cliente a cópia da decisão",
        object=CASE,
        expected_operation="deliver_document",
        due_at=due,
    )
    assert restates_on_object("Enviar cópia da sentença ao cliente", due, CASE, "deliver_document", sent)
    # Undated, the words must all be there: one word apart is another thing to send.
    assert not restates_on_object("Enviar cópia da sentença ao cliente", None, CASE, "deliver_document", sent)
    # Two words apart, or two words in all, stay two promises.
    assert not restates_on_object(
        "Enviar comprovante de pagamento ao cliente", due, CASE, "deliver_document", sent
    )
    planta = replace(sent, description="Enviar a planta")
    assert not restates_on_object("Enviar o habite-se", due, CASE, "deliver_document", planta)


def test_a_retelling_that_names_the_object_again_is_the_same_promise() -> None:
    # Round 6: "Enviar cópia da sentença do processo 1013456-86.2026.8.26.0002 até 07/10" (the attendant's
    # echo) and "Enviar ao cliente cópia da decisão" (the lawyer's) on that case, both deliver_document by
    # 07/10, stayed two promises: "processo" and its number were a stem the lawyer's words did not say.
    due = T0 + timedelta(days=1)
    sent = item(
        description="Enviar ao cliente cópia da decisão",
        object=CASE,
        expected_operation="deliver_document",
        due_at=due,
    )
    told = "Enviar cópia da sentença do processo 1013456-86.2026.8.26.0002 até 07/10"
    assert restates_on_object(told, due, CASE, "deliver_document", sent)
    # Two notices on one case the same day stay two: two words apart is another thing to do.
    hearing = replace(
        sent, description="Avisar o cliente da audiência de conciliação", expected_operation="notify_client"
    )
    assert not restates_on_object(
        "Avisar o cliente da intimação para contestar no processo 1013456-86.2026.8.26.0002",
        due,
        CASE,
        "notify_client",
        hearing,
    )
    assert not restates_on_object(
        "Enviar comprovante de pagamento do pedido 48213 ao cliente", due, CASE, "deliver_document", sent
    )


def test_a_retelling_the_words_cannot_settle_is_put_to_the_decision_model() -> None:
    # Round 7: "Enviar ao financeiro comprovante do pagamento das custas" (the attendant) and "Enviar
    # comprovante do pagamento ao financeiro do cliente" (the lawyer) on the case, and "Enviar cópia da
    # sentença" undated for "Enviar ao cliente a cópia da decisão": two promises each.
    due = T0 + timedelta(days=1)
    receipt = item(
        description="Enviar comprovante do pagamento ao financeiro do cliente",
        object=CASE,
        expected_operation="deliver_document",
        due_at=due,
    )
    told = "Enviar ao financeiro comprovante do pagamento das custas"
    assert not restates_on_object(told, None, CASE, "deliver_document", receipt)
    assert to_judge_retelling(told, None, CASE, "deliver_document", receipt)
    copy = replace(receipt, description="Enviar ao cliente a cópia da decisão")
    assert to_judge_retelling("Enviar cópia da sentença", None, CASE, "deliver_document", copy)
    # What the words settle, or what cannot be the same promise, is never asked.
    assert not to_judge_retelling("Enviar cópia da decisão", due, CASE, "deliver_document", copy)
    assert not to_judge_retelling(told, None, CASE, "notify_client", receipt)
    assert not to_judge_retelling(told, None, INVOICE, "deliver_document", receipt)
    assert not to_judge_retelling(told, due + timedelta(days=3), CASE, "deliver_document", receipt)
    # Told waiting for no operation, it is asked as well.
    assert to_judge_retelling(told, None, CASE, None, receipt)
    resolved = replace(receipt, status=OpenItemStatus.RESOLVED)
    assert not to_judge_retelling(told, None, CASE, "deliver_document", resolved)
    # Nothing in common but the act: another thing to send.
    plan = replace(receipt, description="Enviar a planta")
    assert not to_judge_retelling("Enviar o habite-se", None, CASE, "deliver_document", plan)


def test_a_retelling_waiting_for_another_operation_on_the_same_thing_is_put_to_the_decision_model() -> None:
    # juridico-zero, round 3: the lawyer's "Enviar ao cliente a cópia da decisão" waited for attach_document,
    # the only document operation observed when it was said, overdue since 06/10; the attendant's
    # "Providenciar e enviar a cópia da decisão da sentença" (deliver_document, undated) opened beside it.
    copy = item(
        description="Enviar ao cliente a cópia da decisão",
        object=CASE,
        expected_operation="attach_document",
        due_at=T0 - timedelta(days=1),
    )
    told = "Providenciar e enviar a cópia da decisão da sentença"
    assert to_judge_retelling(told, None, CASE, "deliver_document", copy)
    # An open item waiting for no operation may be told again too; one family, as the closings take it.
    waiting = replace(copy, expected_operation=None)
    assert to_judge_retelling(told, None, CASE, "deliver_document", waiting)
    assert to_judge_retelling(told, None, CASE, "attach_document", replace(copy, expected_operation="attach"))
    # Done to another kind of thing, it is never asked, and the words alone never settle it.
    fees = replace(copy, expected_operation="pay_court_fees")
    assert not to_judge_retelling(told, None, CASE, "deliver_document", fees)
    assert not restates_on_object(
        "Enviar ao cliente a cópia da decisão", None, CASE, "deliver_document", copy
    )
    assert to_judge_retelling(told, None, CASE, None, waiting)


def test_two_items_waiting_for_no_operation_with_the_same_words_on_the_object_are_one() -> None:
    # juridico-zero, round 4: the lawyer's "Enviar comprovante de pagamento ao financeiro do cliente" and the
    # attendant's "Enviar o comprovante de pagamento ao financeiro do cliente", both on the case by 13/10 and
    # waiting for no operation (none was observed when they were said), were two open promises.
    due = T0 + timedelta(days=5)
    receipt = item(
        description="Enviar comprovante de pagamento ao financeiro do cliente",
        object=CASE,
        expected_operation=None,
        due_at=due,
    )
    told = "Enviar o comprovante de pagamento ao financeiro do cliente"
    assert restates_on_object(told, due, CASE, None, receipt)
    assert restates_on_object(told, None, CASE, None, receipt)
    # Waiting for none on one side, the words must be the same ones and say enough: a short notice is not a
    # longer one, a word more is another thing, another day another promise, and another object never.
    notice = replace(receipt, description="Avisar o cliente da audiência de conciliação")
    assert not restates_on_object("Avisar o cliente", due, CASE, None, notice)
    assert not restates_on_object(told + " e a nota fiscal", due, CASE, None, receipt)
    assert not restates_on_object(told, due + timedelta(days=3), CASE, None, receipt)
    assert not restates_on_object(told, due, INVOICE, None, receipt)
    # One word apart is not settled by the words when an operation is missing: it goes to the model.
    other = "Enviar comprovante de pagamento ao financeiro da empresa"
    assert not restates_on_object(other, due, CASE, None, receipt)
    assert to_judge_retelling(other, due, CASE, None, receipt)


def test_a_closing_built_from_a_customer_facing_agents_action_is_strict_until_confirmed() -> None:
    action = AgentAction(
        UUID(int=9), UUID(int=10), SOURCE, "credit", ActionStatus.DECLARED, T0, result="crédito lançado"
    )
    strict = Closing.of_action(action, INVOICE, {"credit"}, customer_facing=True)
    assert (strict.strict, strict.said) == (True, "crédito lançado")
    assert not Closing.of_action(action, INVOICE, {"credit"}).strict
    confirmed = replace(action, status=ActionStatus.CONFIRMED)
    assert not Closing.of_action(confirmed, INVOICE, {"credit"}, customer_facing=True).strict


def test_overdue_only_when_open_and_past_due() -> None:
    assert is_overdue(item(due_at=T0 - timedelta(hours=1)), T0)
    assert not is_overdue(item(due_at=T0 + timedelta(hours=1)), T0)
    assert not is_overdue(item(due_at=None), T0)


def test_a_due_date_with_no_time_of_day_is_due_until_that_day_ends_in_the_spaces_zone() -> None:
    # "até 03/10" is kept as the start of 03/10 in São Paulo (03:00 UTC): at 00:06 there it is not late yet,
    # and it is once that day ends there.
    zone = "America/Sao_Paulo"
    by_the_3rd = item(due_at=datetime(2026, 10, 3, 3, 0, tzinfo=UTC))
    assert not is_overdue(by_the_3rd, datetime(2026, 10, 3, 3, 6, tzinfo=UTC), zone)
    assert not is_overdue(by_the_3rd, datetime(2026, 10, 4, 2, 59, tzinfo=UTC), zone)
    assert is_overdue(by_the_3rd, datetime(2026, 10, 4, 3, 1, tzinfo=UTC), zone)
    # A due date with a time of day is due at that time.
    at_two_pm = item(due_at=datetime(2026, 10, 3, 17, 0, tzinfo=UTC))
    assert is_overdue(at_two_pm, datetime(2026, 10, 3, 17, 1, tzinfo=UTC), zone)


def attr(value: str, minutes: int, source: UUID = SOURCE, times: int = 1) -> ProfileAttr:
    return ProfileAttr(H1, "city", value, source, T0 + timedelta(minutes=minutes), times)


def test_slot_rows_keep_the_latest_value_and_count_repeats() -> None:
    assert merge_attr(None, attr("Recife", 0)) == attr("Recife", 0)
    assert merge_attr(attr("Recife", 5), attr("recife", 9)) == attr("Recife", 9, times=2)
    assert merge_attr(attr("Recife", 5), attr("Natal", 1)) is None
    assert merge_attr(attr("Recife", 5), attr("Recife", 5)) is None
    assert merge_attr(attr("Recife", 5), attr("Natal", 9)) == attr("Natal", 9)


def test_survivorship_rules() -> None:
    other = UUID(int=3)
    rows = [attr("Recife", 1, times=5), attr("Natal", 9, source=other)]
    assert survive(rows, "latest").value == "Natal"  # type: ignore[union-attr]
    assert survive(rows, "most_frequent").value == "Recife"  # type: ignore[union-attr]
    assert survive(rows, "source_priority", {SOURCE: 0, other: 1}).value == "Recife"  # type: ignore[union-attr]
    assert survive([], "latest") is None


def test_stable_ids_are_uuid7_deterministic_and_time_ordered() -> None:
    a = stable_uuid7(T0, "session", "episode")
    assert a == stable_uuid7(T0, "session", "episode")
    assert a != stable_uuid7(T0, "session", "other")
    assert a.version == 7
    assert uuid7_ms(a) == int(T0.timestamp() * 1000)


def test_tail_summaries_are_deterministic_and_count_recurrence() -> None:
    def episode(day: int, category: str) -> Episode:
        at = T0 + timedelta(days=day)
        return Episode(
            UUID(int=day + 1),
            1,
            H1,
            UUID(int=50 + day),
            "whatsapp",
            SOURCE,
            f"s{day}",
            "complaint",
            category,
            category,
            "resolved",
            at,
            at,
        )

    episodes = [
        episode(d, c) for d, c in [(0, "billing"), (1, "billing"), (2, "technical_visit"), (3, "billing")]
    ]
    texts = tail_summaries(list(reversed(episodes)), "UTC")
    assert texts == tail_summaries(episodes, "UTC")
    assert texts["short"].splitlines()[0] == "2026-09-25 whatsapp billing: s3 [resolved]"
    assert len(texts["short"].splitlines()) == 3
    assert texts["long"].splitlines()[0] == "billing x3, technical_visit x1"


LEGAL_CASE = ObjectKey("legal_case", "legaldesk", "c-1")


def test_an_item_that_waits_for_no_operation_closes_by_what_is_said_on_its_object() -> None:
    # juridico-zero (07/10/2026): the lawyer's "avisar o cliente sobre a intimação" was extracted before any
    # agent had recorded `notify_client`, so it waited for no operation and no notice ever closed it.
    promise = item(
        description="Avisar o cliente sobre a intimação para contestar",
        object=LEGAL_CASE,
        expected_operation=None,
    )
    notice = closing(
        ActionStatus.DECLARED,
        operation="notify_client",
        object=LEGAL_CASE,
        trusted=True,
        strict=True,
        said="Aviso sobre a intimação para apresentar contestação no prazo de 15 dias",
    )
    assert closes(promise, notice)
    assert not closes(promise, replace(notice, object=ObjectKey("legal_case", "legaldesk", "c-2")))
    assert not closes(promise, replace(notice, said="Informar que a contestação foi protocolada"))
    # A system event saying nothing of it does not close it on the object and operation alone.
    assert not closes(promise, closing(operation="file", object=LEGAL_CASE, said="filing protocolled"))


def test_a_system_event_of_the_items_family_is_put_to_the_model_by_what_it_records() -> None:
    # The model named "Recolher as custas do preparo" `pay`; the system confirmed `pay_court_fees` with
    # `custas.paid`. The family and the words it records put it to the decision model; only a yes closes.
    fees = item(description="Recolher as custas do preparo", object=LEGAL_CASE, expected_operation="pay")
    paid = closing(operation="pay_court_fees", object=LEGAL_CASE, said=recorded_words("custas.paid"))
    assert paid.said == "custas paid"
    assert not closes(fees, paid)
    assert to_judge(fees, paid)
    assert closes(fees, paid, judged=True)
    # Another family, or words of nothing in the item, are never put to the model.
    assert not to_judge(fees, replace(paid, operation="attach_document"))
    assert not to_judge(fees, replace(paid, said="hearing confirmed"))


def test_a_promise_done_late_closes_on_its_words_and_the_operation_alone_only_in_the_window() -> None:
    # "Protocolar a réplica" due 25/09 and filed on 05/10 closed nothing, and the case read
    # "atrasada" forever. A closing that says the item's words does it whenever it comes; the same operation
    # on the same object with no word of it still closes only in the window.
    late = T0 + timedelta(days=13)
    promise = item(
        description="Protocolar a réplica",
        object=CASE,
        expected_operation="file",
        due_at=T0 + timedelta(days=3),
    )
    filed = Closing("file", late, ActionStatus.CONFIRMED, object=CASE, said="réplica protocolada")
    assert closes(promise, filed)
    mute = Closing("file", late, ActionStatus.CONFIRMED, object=CASE)
    assert not closes(promise, mute)
    assert closes(promise, replace(mute, occurred_at=T0 + timedelta(days=4)))
    # Never what was done before the promise, beyond the window.
    early = replace(filed, occurred_at=T0 - timedelta(days=10))
    assert not closes(promise, early)


def test_an_item_about_no_object_closes_on_what_the_closing_says_and_never_on_the_operation_alone() -> None:
    # An item with no object never closed from what an action says. The closing pass reads
    # the customer's own items, so the customer scopes it; the words, or the model's yes, decide.
    promise = item(description="Enviar o boleto atualizado", object=None, expected_operation=None)
    sent = Closing(
        "deliver_document",
        T0 + timedelta(days=5),
        ActionStatus.CONFIRMED,
        object=INVOICE,
        said="Boleto atualizado enviado ao cliente",
    )
    assert closes(promise, sent)
    other = replace(sent, said="Segunda via da fatura enviada")
    assert not closes(promise, other)
    assert not to_judge(promise, other)  # nothing of the item named: never asked
    asked = replace(sent, said="Mandei o boleto pelo WhatsApp")
    assert to_judge(promise, asked)
    assert closes(promise, asked, judged=True)
    assert not closes(promise, Closing("deliver_document", T0, ActionStatus.CONFIRMED))
