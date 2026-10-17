"""The ingest boundary (WP-A): cards, security codes, passwords and URL credentials are held back; the
numbers memory needs (protocols, orders, postal codes, amounts, dates) never are."""

import orjson
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from niadra.domain.ingest.masking import (
    ALL_TYPES,
    Held,
    added,
    fingerprint,
    language,
    marker,
    mask_fields,
    mask_payload,
    mask_text,
    needs_context,
)
from niadra.domain.memory.values import find_values, locate

PAN = "4111111111111111"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("meu cartão é 4111 1111 1111 1111", "meu cartão é [retido:cartão ****1111]"),
        ("cartão 4111.1111.1111.1111", "cartão [retido:cartão ****1111]"),
        ("4111-1111-1111-1111 é o meu", "[retido:cartão ****1111] é o meu"),
        ("o número do cartão: 4111111111111111", "o número do cartão: [retido:cartão ****1111]"),
        ("my card is 5555555555554444", "my card is [retido:cartão ****4444]"),
        ("amex 3782 822463 10005", "amex [retido:cartão ****0005]"),
        ("tarjeta 4111111111111111 por favor", "tarjeta [retido:cartão ****1111] por favor"),
        ("4111111111111111 cvv 123", "[retido:cartão ****1111] cvv [retido:cvv]"),
    ],
)
def test_a_card_is_held_with_its_last_four_digits(text: str, expected: str) -> None:
    masked = mask_text(text)
    assert masked.text == expected
    assert masked.counts["card"] == 1


@pytest.mark.parametrize(
    "text",
    [
        "o protocolo é 45778204",
        "protocolo 45778204, pedido 4111111111111111",
        "pedido 4111111111111111 foi pago no cartão",
        "order 4111111111111111 shipped",
        "segue o boleto 4111111111111111",
        "4111111111111111",  # bare: no word of its own and no turn before it
        "meu CEP é 01310-100 e o valor R$ 1.234,56 no dia 10/09 às 14:30",
        "cartão final 1234",
        "o cvv? meu cep é 01310-100",
        "Pedido 12345678901234567890 entregue",
        "número 4111111111111112 no cartão",  # fails the check digit
    ],
)
def test_what_memory_needs_is_never_touched(text: str) -> None:
    assert mask_text(text).text == text


def test_the_deterministic_net_still_finds_the_protocol_in_masked_text() -> None:
    text = "Protocolo 45778204. Meu cartão é 4111 1111 1111 1111 e o CEP 01310-100"
    masked = mask_text(text).text
    found = {(v.type.value, v.raw) for v in find_values(masked)}
    assert ("protocol", "45778204") in found
    assert ("postal_code", "01310-100") in found
    # A marker is opaque to the net: its last four digits are never a value, and an extracted "1111"
    # is never located inside it.
    assert not any("1111" in raw for _, raw in found)
    assert locate(masked, "1111") is None
    assert locate("pedido [retido:cartão ****1111]", "1111") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("o cvv é 123", "o cvv é [retido:cvv]"),
        ("CVV: 9876", "CVV: [retido:cvv]"),
        ("security code 321", "security code [retido:cvv]"),
        ("código de segurança 456", "código de segurança [retido:cvv]"),
        ("codigo de seguridad: 789", "codigo de seguridad: [retido:cvv]"),
        ("123 é o cvv", "[retido:cvv] é o cvv"),
        ("cvv 123 e o CEP 01310-100", "cvv [retido:cvv] e o CEP 01310-100"),
    ],
)
def test_a_security_code_is_held_only_next_to_its_word(text: str, expected: str) -> None:
    assert mask_text(text).text == expected


@pytest.mark.parametrize(
    "text",
    [
        "o código é 123",
        "chegaram 123 unidades",
        "cvv? o pedido é 1234",
        "cvv R$ 120",
        "cvv 12/29",
    ],
)
def test_three_digits_without_the_word_stay(text: str) -> None:
    assert mask_text(text).text == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("minha senha é hunter2.", "minha senha é [retido:senha]."),
        ("senha do app: abc@123", "senha do app: [retido:senha]"),
        ("Senha=segredo42", "Senha=[retido:senha]"),
        ("my password is hunter2", "my password is [retido:senha]"),
        ("mi contraseña es gato123", "mi contraseña es [retido:senha]"),
        ('a senha é "abc 123"', 'a senha é "[retido:senha]"'),
        ("PIN: 9876", "PIN: [retido:senha]"),
    ],
)
def test_a_password_is_held_only_after_its_word(text: str, expected: str) -> None:
    assert mask_text(text).text == expected


@pytest.mark.parametrize(
    "text",
    [
        "esqueci minha senha, pode resetar?",
        "a senha é muito difícil",
        "senha e usuário: joao",
        "hunter2",
        "a palavra clave es la puntualidad",
    ],
)
def test_talk_about_a_password_is_not_one(text: str) -> None:
    assert mask_text(text).text == text


def test_url_credentials_are_a_secret_and_the_address_stays() -> None:
    masked = mask_text("acesse https://joao:segredo123@intranet.example.com/x")
    assert masked.text == "acesse https://[retido:segredo]@intranet.example.com/x"
    assert masked.counts == {"secret": 1}


def test_terminal_escapes_and_control_characters_go() -> None:
    assert (
        mask_text("\x1b[31mvermelho\x1b[0m e NUL\x00 aqui\ttab\nlinha").text
        == "vermelho e NUL aqui\ttab\nlinha"
    )


def test_a_bare_card_number_takes_its_meaning_from_the_turn_before() -> None:
    assert needs_context(PAN)
    assert not needs_context("pedido " + PAN)
    assert not needs_context("protocolo 45778204")
    assert mask_text(PAN, context="Qual o número do cartão?").text == "[retido:cartão ****1111]"
    assert mask_text(PAN, context="Qual o número do pedido?").text == PAN
    # The number's own word wins over the turn before.
    assert mask_text("pedido " + PAN, context="Qual o cartão?").text == "pedido " + PAN


def test_markers_follow_the_space_language_and_only_the_chosen_types() -> None:
    text = "cartão 4111 1111 1111 1111 senha: abc"
    assert mask_text(text, "en").text == "cartão [withheld:card ****1111] senha: [withheld:password]"
    assert mask_text(text, "es").text == "cartão [retenido:tarjeta ****1111] senha: [retenido:contraseña]"
    assert mask_text(text, types={Held.PASSWORD}).text == "cartão 4111 1111 1111 1111 senha: [retido:senha]"
    assert [language("pt-BR"), language("es_MX"), language("fr-FR")] == ["pt", "es", "en"]
    assert marker(Held.CARD, "pt", "1234") == "[retido:cartão ****1234]"


@pytest.mark.parametrize(
    "text",
    [
        "meu cartão é 4111 1111 1111 1111 cvv 123 validade 12/29, senha: abc",
        "cvv 123 venc 1229",
        "123 é o cvv",
        "senha: [retido:senha] 4532015112830366 cartão",
        "[retido:cvv] 999 cvv",
    ],
)
def test_masking_masked_text_changes_nothing(text: str) -> None:
    once = mask_text(text)
    twice = mask_text(once.text)
    assert twice.text == once.text
    assert twice.held == ()


_WORDS = st.sampled_from(
    [
        "cartão",
        "card",
        "tarjeta",
        "pedido",
        "protocolo",
        "cvv",
        "senha",
        "senha:",
        "password",
        "é",
        "is",
        "o",
        "meu",
        "e",
        "4111",
        "1111",
        "4111111111111111",
        "5555555555554444",
        "45778204",
        "01310-100",
        "123",
        "1234",
        "12/29",
        "R$",
        "1.234,56",
        "hunter2",
        "https://a:b@h",
        "[retido:cvv]",
        "[retido:cartão ****1111]",
        ":",
        ",",
        "\n",
    ]
)


@settings(max_examples=300, deadline=None)
@given(st.lists(_WORDS, max_size=16), st.sampled_from(["pt", "en", "es"]))
def test_masking_is_idempotent_whatever_the_words(words: list[str], lang: str) -> None:
    text = " ".join(words)
    once = mask_text(text, lang)
    assert mask_text(once.text, lang).text == once.text
    # A number that passes the card check never survives right after a card word.
    assert f"cartão {PAN}" not in once.text
    assert f"card {PAN}" not in once.text


def test_fields_are_masked_by_their_text_and_by_their_names() -> None:
    fields = {
        "card_number": 4111111111111111,
        "cardNumber": "4111 1111 1111 1111",
        "order_id": PAN,
        "cvv": "123",
        "security_code": 456,
        "password": "abc",
        "api_key": "k-1",
        "notes": "senha: x1",
        "amount": "123.45",
        "zip": "01310-100",
        "card_last4": "1111",
        "items": [{"description": "cartão 4111 1111 1111 1111"}],
        "paid": True,
    }
    masked, held = mask_fields(fields)
    assert masked == {
        "card_number": "[retido:cartão ****1111]",
        "cardNumber": "[retido:cartão ****1111]",
        "order_id": PAN,
        "cvv": "[retido:cvv]",
        "security_code": "[retido:cvv]",
        "password": "[retido:senha]",
        "api_key": "[retido:segredo]",
        "notes": "senha: [retido:senha]",
        "amount": "123.45",
        "zip": "01310-100",
        "card_last4": "1111",
        "items": [{"description": "cartão [retido:cartão ****1111]"}],
        "paid": True,
    }
    assert added({"card": 1}, held) == {"card": 4, "cvv": 2, "password": 2, "secret": 1}
    assert mask_fields(masked) == (masked, ())


def test_a_raw_payload_keeps_its_shape_and_masks_the_cards_its_events_held() -> None:
    body = orjson.dumps({"event": "paid", "text": PAN, "card": {"number": "4111 1111 1111 1111"}, "id": PAN})
    masked, held = mask_payload(body, known_cards={PAN})
    assert orjson.loads(masked) == {
        "event": "paid",
        "text": "[retido:cartão ****1111]",
        "card": {"number": "[retido:cartão ****1111]"},
        "id": "[retido:cartão ****1111]",
    }
    assert len(held) == 3
    untouched = orjson.dumps({"order": PAN})
    assert mask_payload(untouched) == (untouched, ())
    text, found = mask_payload(b"cartao 4111 1111 1111 1111 \xff")
    assert text == "cartao [retido:cartão ****1111] ".encode() + b"\xff"
    assert [h.kind for h in found] == [Held.CARD]


def test_a_held_card_never_shows_its_digits_in_a_repr() -> None:
    [hold] = mask_text("cartão " + PAN).held
    assert hold.digits == PAN
    assert PAN not in repr(hold)


def test_the_fingerprint_follows_the_rules_and_the_types() -> None:
    assert fingerprint(ALL_TYPES) == fingerprint(sorted(ALL_TYPES))
    assert fingerprint(ALL_TYPES) != fingerprint({Held.CARD})
    assert 0 <= fingerprint(ALL_TYPES) < 2**63


def test_a_long_run_of_numbers_is_scanned_in_linear_time() -> None:
    text = " ".join(["1234"] * 20_000)
    assert mask_text(text).text == text
