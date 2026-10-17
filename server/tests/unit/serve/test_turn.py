"""What a customer's turn asks of memory, read by rules in Portuguese, English and Spanish (11, 3.3)."""

import pytest

from niadra.domain.serve.turn import Anchor, QuestionKind, analyze, identifiers_in, typed_values


@pytest.mark.parametrize(
    ("text", "types", "question", "channels"),
    [
        (
            "Mandei um e-mail esses dias. Qual é o número de protocolo que me passaram?",
            ["protocol"],
            "which",
            ["email"],
        ),
        (
            "I sent you an email the other day. What protocol number did you give me?",
            ["protocol"],
            "what",
            ["email"],
        ),
        ("Les escribí un correo. ¿Cuál es el número de protocolo?", ["protocol"], "which", ["email"]),
        (
            "I emailed you about my claim. What protocol number did you give me?",
            ["protocol", "claim"],
            "what",
            ["email"],
        ),
        ("Qual é o CEP de coleta que vocês têm hoje no meu cadastro?", ["postal_code"], "which", []),
        ("What is the pickup ZIP code you have on file now?", ["postal_code"], "which", []),
        ("¿Cuál es el código postal de recogida que tienen?", ["postal_code"], "which", []),
        (
            "O estorno que vocês me prometeram saiu? Qual é o número do comprovante?",
            ["receipt"],
            "status",
            [],
        ),
        ("Did the refund you promised go through? What is the receipt number?", ["receipt"], "which", []),
        ("¿Ya salió el reembolso? ¿Cuál es el número del comprobante?", ["receipt"], "status", []),
        ("Quantas vezes eu já reclamei da internet caindo antes desta ligação?", [], "count", []),
        ("How many times have I complained about the internet dropping before this call?", [], "count", []),
        ("¿Cuántas veces me quejé de la entrega?", [], "count", []),
        ("Até que dia eu disse no WhatsApp que precisava da solução?", [], "when", ["whatsapp"]),
        ("By what day did I say on WhatsApp I needed it solved?", [], "when", ["whatsapp"]),
        ("What did I report after the inspection? What was the reference number?", ["record"], "which", []),
    ],
)
def test_the_turn_says_what_it_asks_for(
    text: str, types: list[str], question: str, channels: list[str]
) -> None:
    turn = analyze(text)
    assert list(turn.asked_types) == types
    assert turn.question.value == question
    assert list(turn.channels) == channels


def test_the_contact_in_progress_is_not_a_channel_the_customer_refers_to() -> None:
    assert analyze("Quantas vezes reclamei antes desta ligação?").channels == ()
    assert analyze("Antes da ligação de ontem, o que eu disse?").channels == ("voice",)


def test_identifiers_are_digits_only_and_dates_are_not_identifiers() -> None:
    assert identifiers_in("O CEP é 45778-204, desde 23/09/2026 às 10:30.") == ("45778204",)
    assert analyze("Sobre o pedido 88.280 de ontem").identifiers == ("88280",)
    assert analyze("Qual é o 45778204?").identifiers == analyze("Qual é o 45778-204?").identifiers


def test_a_value_belongs_to_the_type_named_near_it() -> None:
    assert typed_values("Olá. O protocolo do seu atendimento é 81220.") == {"protocol": ("81220",)}
    assert typed_values("Registrei como 88280.") == {"record": ("88280",)}
    assert typed_values("dispute 23805 · amount: 112.64 · receipt: 23058")["receipt"] == (
        "23805",
        "11264",
        "23058",
    )
    assert typed_values("O protocolo foi enviado ontem.") == {}


def test_time_words_are_phrases_a_calendar_reads_and_never_a_word_that_parses_by_accident() -> None:
    assert analyze("O que eu falei semana passada sobre a fatura?").time_phrases == ("semana passada",)
    assert analyze("What did I say last month?").time_phrases == ("last month",)
    assert analyze("¿Qué dije ayer?").time_phrases == ("ayer",)
    # "segunda via" is a duplicate invoice, not Monday; "out" and "may" are not months.
    assert analyze("Quero a segunda via da conta").time_phrases == ()
    assert analyze("May I check it out?").time_phrases == ()
    assert analyze("O que eu disse na segunda?").time_phrases == ("na segunda",)


def test_anchors_name_the_relation_and_the_event_word() -> None:
    assert analyze("O que eu relatei depois da vistoria?").anchors == (Anchor("after", "vistoria"),)
    assert analyze("What did I report after the new card was issued?").anchors == (Anchor("after", "card"),)
    assert analyze("¿Qué pasó antes de la entrega?").anchors == (Anchor("before", "entrega"),)
    assert analyze("Desde a instalação a internet cai").anchors == (Anchor("since", "instalacao"),)


def test_the_topic_keeps_content_words_only() -> None:
    turn = analyze("Quantas vezes eu já reclamei da internet caindo com vocês antes desta ligação?")
    assert turn.topic == ("reclamei", "internet", "caindo", "antes", "ligacao")
    assert turn.question is QuestionKind.COUNT


def test_the_manifest_never_carries_the_customers_words_or_numbers() -> None:
    turn = analyze("Mandei um e-mail semana passada sobre o pedido 88280. Qual o protocolo?")
    manifest = turn.manifest()
    assert manifest == {
        "question": "which",
        "asked_types": ["protocol", "order"],
        "channels": ["email"],
        "identifiers": 1,
        "topic_terms": manifest["topic_terms"],
        "time_phrases": 1,
        "anchors": [],
    }
    assert "88280" not in str(manifest)
    assert "pedido" not in str(manifest)


def test_an_empty_turn_asks_nothing() -> None:
    assert analyze(None).empty
    assert analyze("").empty
    assert analyze("oi, tudo bem?").empty
