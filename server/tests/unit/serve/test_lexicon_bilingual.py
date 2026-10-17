"""Summaries in the customer's language (17, 6.9): a space in Portuguese with customers who write in English
keeps their conversations in English, and the lexical channel still reads them from a question in either
language, through the stems of both and the starter's concept groups."""

from datetime import UTC, datetime, timedelta

import pytest

from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.serve.slots import SlotItem, run_channels, standard_channels
from niadra.domain.serve.turn import analyze
from niadra.testing.cell import sample_config

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
LEXICON = sample_config().lexicon()


def item(n: int, text: str) -> SlotItem:
    line = f"[Conversa] {text}"
    return SlotItem(
        f"episode:{n:04d}", "episode", text, line, estimate_tokens(line + "\n"), at=NOW - timedelta(days=n)
    )


POOL = [
    item(
        1, "The customer says the internet dropped again in the middle of the night. Incident 49185 logged."
    ),
    item(2, "Cliente relata que a entrega não foi feita de novo. Ocorrência 49688 registrada."),
    item(3, "Customer asked about a plan with more data; offer 26709."),
    item(4, "Cliente pediu a segunda via da fatura 36708."),
]


def lexical(question: str) -> tuple[str, ...]:
    [hits] = [
        h
        for h in run_channels(analyze(question), POOL, standard_channels((), lexicon=LEXICON))
        if h.channel == "lexical"
    ]
    return hits.ranked


@pytest.mark.parametrize(
    ("question", "first"),
    [
        ("How many times did my internet drop?", "episode:0001"),
        ("Quantas vezes a minha internet caiu?", "episode:0001"),  # a Portuguese question, an English line
        # An English question, a Portuguese line.
        ("How many times was my delivery not made?", "episode:0002"),
        ("Quantas vezes a entrega falhou?", "episode:0002"),
    ],
)
def test_a_question_in_either_language_finds_the_line_in_the_other(question: str, first: str) -> None:
    assert lexical(question)[0] == first
