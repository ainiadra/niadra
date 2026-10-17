"""A fact's weight in the pack: confirmation by distinct sessions on a saturating
curve, the legacy reading of a row not counted yet, and the similarity band's penalty while a pair is live."""

import math
from dataclasses import replace

from hypothesis import given
from hypothesis import strategies as st

from niadra.domain.compile import CompileInput, Variant
from niadra.domain.compile.candidates import (
    BAND_PENALTY,
    CandidateBuilder,
    _confirmation,
)
from niadra.domain.compile.scoring import Focus, score
from niadra.domain.compile.templates import Formatter
from niadra.domain.records import Fact
from tests.unit.compile.factories import fact, marina, settings

NOTE = fact(1, "contact_preference", "prefere ser atendido de manhã", "contact")


@given(st.integers(min_value=0, max_value=10_000))
def test_the_curve_never_falls_and_never_passes_one_and_a_half(n: int) -> None:
    assert 1.0 <= _confirmation(n) <= 1.5
    assert _confirmation(n + 1) >= _confirmation(n)


def test_the_first_sessions_count_most() -> None:
    steps = [_confirmation(n + 1) - _confirmation(n) for n in range(6)]
    assert steps == sorted(steps, reverse=True)
    assert math.isclose(_confirmation(3), 1.0 + 0.5 * (1 - math.exp(-1)))
    # Ten sessions are worth little more than five: repetition saturates.
    assert _confirmation(10) - _confirmation(5) < _confirmation(2) - _confirmation(1)


def weights(*facts: Fact, contradicted: frozenset = frozenset()) -> dict[str, float]:  # type: ignore[type-arg]
    snapshot = replace(marina(), facts=facts, band_contradicted=contradicted)
    built = CandidateBuilder(CompileInput(snapshot, Variant(), settings()), Formatter("UTC", "pt-BR")).build()
    return {c.item_id: c.weight for c in built if c.item_id.startswith("fact:")}


def test_candidates_with_one_three_and_ten_sessions_rank_in_that_order() -> None:
    one = replace(NOTE, sessions_seen=1, times_seen=40)
    three = replace(fact(2, "contact_preference", "prefere e-mail", "contact"), sessions_seen=3, times_seen=3)
    ten = replace(
        fact(3, "contact_preference", "prefere o gerente", "contact"), sessions_seen=10, times_seen=10
    )
    got = weights(one, three, ten)
    assert got[f"fact:{one.fact_id}"] < got[f"fact:{three.fact_id}"] < got[f"fact:{ten.fact_id}"]
    # Forty mentions in one conversation confirm the value as one session does, no more.
    assert got[f"fact:{one.fact_id}"] == NOTE.confidence * _confirmation(1)


def test_a_live_band_pair_costs_each_fact_a_fifth_once() -> None:
    other = replace(fact(2, "contact_preference", "prefere ser atendido à noite", "contact"), sessions_seen=1)
    plain = weights(NOTE, other)
    penalized = weights(NOTE, other, contradicted=frozenset({NOTE.fact_id}))
    key = f"fact:{NOTE.fact_id}"
    assert math.isclose(penalized[key], plain[key] * BAND_PENALTY)
    assert penalized[f"fact:{other.fact_id}"] == plain[f"fact:{other.fact_id}"]


def test_the_weight_moves_the_score_and_nothing_else() -> None:
    snapshot = replace(marina(), facts=(NOTE,))
    [plain] = [
        c
        for c in CandidateBuilder(
            CompileInput(snapshot, Variant(), settings()), Formatter("UTC", "pt-BR")
        ).build()
        if c.item_id == f"fact:{NOTE.fact_id}"
    ]
    penalized = replace(plain, weight=plain.weight * BAND_PENALTY)
    assert penalized.text == plain.text
    assert score(penalized, Focus(plain.at)) < score(plain, Focus(plain.at))
