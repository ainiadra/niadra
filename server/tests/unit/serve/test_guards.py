"""Guard lines (13, 6.1): which value a guard states, when a turn gets one, what the line says, and when
the agent's answer went against it."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from niadra.domain.compile.templates import Formatter
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.serve.guards import (
    CONTROL_ARM,
    CONTROL_FRACTION,
    GUARDS_ARM,
    MAX_TOKENS,
    amount,
    arm,
    authority,
    date_parts,
    guards_for,
    same,
    short_id,
    turn_types,
    violates,
)
from niadra.domain.serve.turn import analyze
from niadra.domain.vocabulary import Speaker

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
PT = Formatter("America/Sao_Paulo", "pt-BR")
EN = Formatter("America/New_York", "en-US")
MEASURED = frozenset({"date", "amount", "protocol"})


def value(
    raw: str,
    speaker: Speaker,
    *,
    days: float,
    type: ValueType = ValueType.DATE,
    n: int = 1,
) -> SettledValue:
    return SettledValue(
        value_id=UUID(int=0x0192F5A0_0000_7000_8000_000000000000 + n),
        origin_handle_id=uuid4(),
        type=type,
        raw=raw,
        said_at=NOW - timedelta(days=days),
        speaker=speaker,
        session_id=uuid4(),
        channel="whatsapp",
        source_id=uuid4(),
        created_at=NOW - timedelta(days=days),
    )


def test_the_system_record_wins_over_a_newer_customer_statement() -> None:
    system = value("18/09/2026", Speaker.SYSTEM, days=9, n=1)
    customer = value("19/09/2026", Speaker.CUSTOMER, days=1, n=2)
    assert authority([customer, system], ValueType.DATE) == system
    [guard] = guards_for(analyze("Quando é a visita?"), [customer, system], MEASURED, fmt=PT, voice=False)
    assert guard.raw == "18/09/2026"
    assert guard.origin is Speaker.SYSTEM
    assert "19/09" not in guard.line


def test_within_the_same_origin_the_latest_statement_holds() -> None:
    older = value("18/09/2026", Speaker.SYSTEM, days=9, n=1)
    newer = value("25/09/2026", Speaker.SYSTEM, days=2, n=2)
    agent = value("30/09/2026", Speaker.HUMAN_AGENT, days=1, n=3)
    assert authority([older, agent, newer], ValueType.DATE) == newer


def test_what_only_the_customer_or_an_ai_agent_said_writes_no_guard() -> None:
    turn = analyze("Quando é a visita?")
    customer = value("19/09/2026", Speaker.CUSTOMER, days=1, n=1)
    ai = value("20/09/2026", Speaker.AI_AGENT, days=1, n=2)
    assert guards_for(turn, [customer, ai], MEASURED, fmt=PT, voice=False) == ()
    assert guards_for(turn, [], MEASURED, fmt=PT, voice=False) == ()


def test_a_human_agent_statement_is_an_authority() -> None:
    human = value("R$ 249,90", Speaker.HUMAN_AGENT, days=3, type=ValueType.AMOUNT)
    [guard] = guards_for(analyze("Qual o valor da parcela?"), [human], MEASURED, fmt=PT, voice=False)
    assert guard.type is ValueType.AMOUNT
    assert guard.rule == "guard_amount"


def test_a_type_the_space_never_measured_a_contradiction_of_writes_no_guard() -> None:
    system = value("81220", Speaker.SYSTEM, days=2, type=ValueType.ORDER)
    turn = analyze("Qual o número do pedido?")
    assert turn_types(turn) == (ValueType.ORDER,)
    assert guards_for(turn, [system], MEASURED, fmt=PT, voice=False) == ()
    assert len(guards_for(turn, [system], MEASURED | {"order"}, fmt=PT, voice=False)) == 1


def test_a_turn_that_asks_nothing_of_the_type_gets_no_guard() -> None:
    system = value("18/09/2026", Speaker.SYSTEM, days=9)
    assert guards_for(analyze("Obrigado, era isso."), [system], MEASURED, fmt=PT, voice=False) == ()


def test_when_asks_for_a_date_without_naming_it() -> None:
    assert turn_types(analyze("Quando o técnico vem?")) == (ValueType.DATE,)
    assert turn_types(analyze("When is the technician coming?")) == (ValueType.DATE,)
    assert turn_types(analyze("¿Cuándo viene el técnico?")) == (ValueType.DATE,)


def test_a_value_holding_a_hold_marker_is_never_stated() -> None:
    held = value("[retido:card] 4242", Speaker.SYSTEM, days=1, type=ValueType.CODE)
    assert authority([held], ValueType.CODE) is None


def test_the_line_carries_the_value_the_origin_and_the_negative_instruction_within_budget() -> None:
    system = value("18/09/2026", Speaker.SYSTEM, days=9)
    [pt] = guards_for(analyze("Quando é a visita?"), [system], MEASURED, fmt=PT, voice=False)
    assert (
        pt.line == "[Guarda] data 18/09/2026, sistema 16/09: não afirme outra; outra do cliente é pendência"
    )
    assert pt.tokens <= MAX_TOKENS
    [en] = guards_for(analyze("When is the visit?"), [system], MEASURED, fmt=EN, voice=True)
    assert en.line.startswith("Guard: date 18/09/2026")
    assert "state no other" in en.line
    assert en.tokens <= MAX_TOKENS


def test_a_long_value_drops_its_origin_then_its_second_clause_and_never_passes_the_budget() -> None:
    long = value("2026091800123456789", Speaker.HUMAN_AGENT, days=1, type=ValueType.PROTOCOL)
    [guard] = guards_for(analyze("Qual o protocolo?"), [long], MEASURED, fmt=PT, voice=False)
    assert "atendente" not in guard.line
    assert "não afirme outro" in guard.line
    assert guard.tokens <= MAX_TOKENS
    huge = value("9" * 90, Speaker.SYSTEM, days=1, type=ValueType.PROTOCOL)
    assert guards_for(analyze("Qual o protocolo?"), [huge], MEASURED, fmt=PT, voice=False) == ()


def test_the_arm_is_stable_and_about_a_fifth_of_customers_are_control() -> None:
    space = uuid4()
    profiles = [UUID(int=i * 7919 + 1) for i in range(2000)]
    arms = [arm(space, p) for p in profiles]
    assert arms == [arm(space, p) for p in profiles]
    share = arms.count(CONTROL_ARM) / len(arms)
    assert abs(share - CONTROL_FRACTION) < 0.04
    assert set(arms) == {CONTROL_ARM, GUARDS_ARM}


def test_short_ids_are_the_last_eight_hex_digits() -> None:
    assert short_id("0192f5a0-0000-7000-8000-0000000000d1") == "000000d1"


@pytest.mark.parametrize(
    ("type", "guarded", "answer", "broken"),
    [
        (ValueType.DATE, "18/09/2026", "Sua visita está marcada para 19/09.", True),
        (ValueType.DATE, "18/09/2026", "Sua visita está marcada para 18/09.", False),
        (ValueType.DATE, "18/09/2026", "Não é 19/09, a visita é 18/09.", False),
        (ValueType.DATE, "2026-09-18", "The visit is on 2026-09-18.", False),
        (ValueType.DATE, "18/09/2026", "Seu pedido 88280 saiu hoje.", False),
        (ValueType.AMOUNT, "R$ 249,90", "A parcela é de R$ 249,90.", False),
        (ValueType.AMOUNT, "R$ 249,90", "A parcela é de R$ 259,90.", True),
        (ValueType.AMOUNT, "R$ 249,90", "O valor da parcela: 249.90 reais.", False),
        (ValueType.PROTOCOL, "81220", "O protocolo do seu atendimento é 81221.", True),
        (ValueType.PROTOCOL, "81220", "O protocolo do seu atendimento é 81.220.", False),
        (ValueType.PROTOCOL, "81220", "Obrigado pela paciência.", False),
    ],
)
def test_an_answer_goes_against_a_guard_only_by_stating_another_value_of_its_type(
    type: ValueType, guarded: str, answer: str, broken: bool
) -> None:
    assert violates(type, guarded, answer) is broken


def test_writings_of_one_value() -> None:
    assert date_parts("18/09") == (18, 9, None)
    assert date_parts("2026-09-18") == (18, 9, 2026)
    assert date_parts("18/09/26") == (18, 9, 2026)
    assert same(ValueType.DATE, "18/09", "18/09/2026")
    assert not same(ValueType.DATE, "18/09/2025", "18/09/2026")
    assert amount("R$ 1.249,90") == amount("$1,249.90")
    assert same(ValueType.AMOUNT, "249,90", "R$249.90")
    assert same(ValueType.PROTOCOL, "45778-204", "45778204")
