"""The intent gate (13, 6.4): closed forms in Portuguese, English and Spanish ask memory for nothing; any
other word leaves the turn to the slots."""

import pytest

from niadra.domain.serve.intent import MAX_WORDS, ClosedForm, gate


@pytest.mark.parametrize(
    ("text", "forms"),
    [
        ("Obrigado!", ["thanks"]),
        ("muito obrigada", ["thanks"]),
        ("Valeu", ["thanks"]),
        ("Thanks a lot!", ["thanks"]),
        ("Thank you so much.", ["thanks"]),
        ("Muchas gracias", ["thanks"]),
        ("ok", ["confirmation"]),
        ("Tá bom", ["confirmation"]),
        ("Got it", ["confirmation"]),
        ("Vale, de acuerdo", ["confirmation"]),
        ("Pode repetir, por favor?", ["repeat"]),
        ("Say that again?", ["repeat"]),
        ("¿Cómo?", ["repeat"]),
        ("Não entendi", ["repeat"]),
        ("Oi, bom dia!", ["greeting"]),
        ("Hello", ["greeting"]),
        ("Buenas tardes", ["greeting"]),
        ("Ok, obrigado!", ["confirmation", "thanks"]),
        ("Sim, perfeito, valeu", ["confirmation", "thanks"]),
        ("Hi, thanks", ["greeting", "thanks"]),
    ],
)
def test_closed_forms_are_gated(text: str, forms: list[str]) -> None:
    found = gate(text)
    assert found is not None
    assert found.manifest() == {"intent": "none", "forms": forms}


@pytest.mark.parametrize(
    "text",
    [
        "e a outra?",
        "E a outra",
        "and the other one?",
        "¿y la otra?",
        "sim, a de quinta",
        "obrigado, e o protocolo?",
        "ok, qual o valor da parcela?",
        "thanks, when is the visit?",
        "o que aconteceu com o meu pedido?",
        "what about my order",
        "oi, meu pedido 88280 chegou?",
        "ok 123",
        "obrigado Maria",
        "por favor",
        "",
        None,
    ],
)
def test_a_word_the_lists_do_not_hold_is_never_gated(text: str | None) -> None:
    assert gate(text) is None


def test_a_long_turn_is_never_gated_even_of_closed_forms() -> None:
    assert gate(" ".join(["ok"] * MAX_WORDS)) is not None
    assert gate(" ".join(["ok"] * (MAX_WORDS + 1))) is None


def test_every_form_has_phrases_in_the_three_languages() -> None:
    assert {f.value for f in ClosedForm} == {"thanks", "confirmation", "repeat", "greeting"}
    for text in ("obrigado", "thanks", "gracias", "certo", "yes", "vale", "repete", "pardon", "repite"):
        assert gate(text) is not None
