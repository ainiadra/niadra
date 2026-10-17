"""Settled values (11, 3.2): the key, the precedence that picks the current one, the deterministic
net and what it must never keep."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from uuid import UUID

import pytest

from niadra.adapters.memory.memory import InMemoryMemoryRepository
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.values import (
    TOKEN,
    SettledValue,
    ValueOrigin,
    ValueType,
    current,
    find_values,
    never_a_value,
    ranked,
    value_key,
)
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.vocabulary import Speaker

T0 = datetime(2026, 9, 20, 12, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c001")


def value(
    raw: str,
    type: ValueType = ValueType.POSTAL_CODE,
    *,
    at: datetime = T0,
    speaker: Speaker = Speaker.CUSTOMER,
    channel: str = "whatsapp",
    origin: UUID | None = None,
    conversation_id: str = "c1",
    evidence: tuple[UUID, ...] = (),
) -> SettledValue:
    return SettledValue(
        value_id=uuid7(),
        origin_handle_id=origin or uuid7(),
        type=type,
        raw=raw,
        said_at=at,
        speaker=speaker,
        session_id=uuid7(),
        channel=channel,
        source_id=SOURCE,
        created_at=T0,
        evidence_event_ids=evidence,
        conversation_id=conversation_id,
        origin=ValueOrigin.RULE,
    )


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("45778-204", "45778204"),
        ("45778-204.", "45.778-204"),
        ("nº 81220", "81220"),
        ("#81220", "N° 81220"),
        ("R$ 112,64", "112,64"),
        ("112,64 reais", "112.64"),
        ("PRT-2026-0042", "prt 2026 0042"),
    ],
)
def test_punctuation_labels_and_currency_never_make_a_new_value(a: str, b: str) -> None:
    assert value_key(a) == value_key(b)


def test_different_numbers_keep_different_keys() -> None:
    assert value_key("45778-204") != value_key("45778-205")
    assert value_key("81220") != value_key("8122")


def test_the_current_value_is_the_latest_statement() -> None:
    old = value("88221-615", at=T0 - timedelta(days=39), channel="email")
    new = value("45778-204", at=T0 - timedelta(days=4))
    again = value("88221615", at=T0 - timedelta(days=60))
    assert current([old, new, again], ValueType.POSTAL_CODE) == new
    # One entry per key, its latest statement.
    assert [v.raw for v in ranked([old, new, again])] == ["45778-204", "88221-615"]
    assert current([old, new], ValueType.POSTAL_CODE, channel="email") == old
    assert current([old, new], ValueType.PROTOCOL) is None


def test_between_statements_of_the_same_moment_the_origin_decides() -> None:
    order = [Speaker.CUSTOMER, Speaker.AI_AGENT, Speaker.HUMAN_AGENT, Speaker.SYSTEM]
    values = [value(f"1000{i}", ValueType.RECORD, speaker=s) for i, s in enumerate(order)]
    for loser, winner in pairwise(values):
        assert current([winner, loser], ValueType.RECORD) == winner
        assert current([loser, winner], ValueType.RECORD) == winner


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("O protocolo do seu atendimento é 81220.", [("protocol", "81220")]),
        ("Escrevo sobre o chamado 82034: a fibra cai.", [("ticket", "82034")]),
        ("Agora o CEP de coleta é 45778-204.", [("postal_code", "45778-204")]),
        ("Mudei de endereço, meu cep 45778204", [("postal_code", "45778204")]),
        ("Registrei como 88280.", [("record", "88280")]),
        ("Seu pedido 5521-A saiu para entrega em 14/09.", [("order", "5521-A"), ("date", "14/09")]),
        ("O comprovante 23058 foi enviado.", [("receipt", "23058")]),
        ("A conta de setembro veio R$ 250,00", [("amount", "250,00")]),
        ("Paguei 112,64 reais ontem", [("amount", "112,64")]),
        ("Tem, o cupom 40997 dá frete grátis.", [("code", "40997")]),
        ("Your order number is 55123 and the refund is $49.90", [("order", "55123"), ("amount", "$49.90")]),
        ("Your ticket 7781 was escalated.", [("ticket", "7781")]),
        (
            "Su pedido 99812 llega mañana, el código de seguimiento es XK-3321.",
            [("order", "99812"), ("code", "XK-3321")],
        ),
        ("Hoje tem 300 pessoas na fila.", []),
    ],
)
def test_the_net_types_a_number_by_the_word_before_it(text: str, expected: list[tuple[str, str]]) -> None:
    assert [(f.type.value, f.raw) for f in find_values(text)] == expected


@pytest.mark.parametrize(
    "text",
    [
        "Meu documento para a nota é 54726939.",
        "Meu CPF é 529.982.247-25",
        "O cartão final 4455 foi recusado.",
        "Cartão de crédito final 4455",
        "Minha conta corrente é 12345-6, agência 0001.",
        "O código de verificação é 482913.",
        "Sua senha provisória é 739201.",
        "My SSN is 123-45-6789.",
        "Mi DNI es 12345678Z.",
        "O pedido saiu no cartão 4111 1111 1111 1111.",
        "Pode ligar no celular 98765-4321.",
        "Seu pedido 52998224725 já foi pago",
    ],
)
def test_documents_cards_accounts_and_secrets_are_never_values(text: str) -> None:
    assert find_values(text) == []


def test_a_check_digit_vetoes_whatever_word_came_before() -> None:
    assert never_a_value("529.982.247-25")  # a valid CPF
    assert never_a_value("4111 1111 1111 1111")  # a valid card number
    assert never_a_value("11.222.333/0001-81")  # a valid CNPJ
    assert not never_a_value("81220")
    assert not never_a_value("45778-204")


def test_a_contact_word_only_vetoes_when_it_is_the_nearest() -> None:
    assert [f.raw for f in find_values("Liguei do celular, o protocolo é 81220.")] == ["81220"]
    assert find_values("Me chama no whatsapp 11 91234 5678") == []


async def test_the_repository_keeps_values_once_and_finds_them_by_key() -> None:
    repo = InMemoryMemoryRepository(MemoryStore())
    marina = uuid7()
    cep = value("45778-204", origin=marina, at=T0)
    protocol = value("81220", ValueType.PROTOCOL, origin=marina, at=T0 - timedelta(days=5), channel="email")
    assert await repo.insert_values([cep, protocol]) == 2
    assert await repo.insert_values([cep]) == 0
    assert await repo.values_for([marina]) == [cep, protocol]
    assert await repo.values_for([marina], types=[ValueType.PROTOCOL]) == [protocol]
    assert await repo.values_for([marina], since=T0 - timedelta(days=1)) == [cep]
    assert await repo.values_by_keys([marina], [value_key("45778204")]) == [cep]
    assert await repo.values_by_keys([uuid7()], [value_key("45778204")]) == []


async def test_values_go_with_their_events_conversations_handles_and_age() -> None:
    repo = InMemoryMemoryRepository(MemoryStore())
    marina, e1 = uuid7(), uuid7()
    by_event = value("81220", ValueType.PROTOCOL, origin=marina, evidence=(e1,), conversation_id="c1")
    by_conversation = value("45778-204", origin=marina, conversation_id="c2")
    old = value("11111", ValueType.CODE, origin=marina, conversation_id="c3", at=T0 - timedelta(days=400))
    kept = value("22222", ValueType.CODE, origin=marina, conversation_id="c3")
    await repo.insert_values([by_event, by_conversation, old, kept])

    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (marina,), None, "c1", T0, event_ids=(e1,))
    assert [r.table for r in await repo.erase_for(scope, limit=10)] == ["settled_values"]
    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (marina,), None, "c2", T0)
    assert [r.key["value_id"] for r in await repo.erase_for(scope, limit=10)] == [
        str(by_conversation.value_id)
    ]
    assert [r.table for r in await repo.expire_episodes(T0 - timedelta(days=365), limit=10)] == [
        "settled_values"
    ]
    assert await repo.values_for([marina]) == [kept]
    scope = ErasureScope(uuid7(), TargetKind.HANDLE, (marina,), None, None, T0)
    assert [r.table for r in await repo.erase_for(scope, limit=10)] == ["settled_values"]
    assert await repo.values_for([marina]) == []


def test_a_value_keeps_its_key_across_a_replace() -> None:
    cep = value("45778-204")
    assert replace(cep, raw="45778204").key == cep.key


# The extraction gate (models server, `rules/decisions.py`, `_VALUE`) counts a value with the same pattern
# the net types here; `models/tests/test_decisions.py` pins the gate's side.
def test_the_net_reads_the_tokens_the_gate_counts() -> None:
    assert TOKEN.pattern == r"(?<![\w])(?=(?:[^\s\d]*\d){3})[\w$.,/:-]*\d[\w.,/:-]*"
    assert TOKEN.findall("A conta veio R$ 250,00 e vence 14/09") == ["250,00", "14/09"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("My ID number for the invoice is 87798164.", []),
        ("The receiver's ID number is 87191204.", []),
        ("O número de identificação é 91277628.", []),
        ("order ID 12345", [(ValueType.ORDER, "12345")]),
        ("ID do pedido 12345", [(ValueType.ORDER, "12345")]),
        ("ID da transação 998877", [(ValueType.RECEIPT, "998877")]),
    ],
)
def test_an_id_number_is_a_document_unless_a_value_word_names_it(
    text: str, expected: list[tuple[ValueType, str]]
) -> None:
    """The benchmark's customers give a document as "my ID number", and the net typed the number by
    "invoice" further along the sentence."""
    assert [(f.type, f.raw) for f in find_values(text)] == expected
