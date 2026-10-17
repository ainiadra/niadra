"""The similarity band between free-text facts of one predicate: a pure function over
facts and their vectors, which only proposes `CONTRADICTS` edges of origin `band`."""

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.domain.common.ids import uuid7
from niadra.domain.compile.model import ItemKind
from niadra.domain.memory.contradictions import band_pairs, opposed
from niadra.domain.memory.facts import Relation, ref
from niadra.domain.memory.vectors import ItemVector
from niadra.domain.records import Fact
from niadra.domain.vocabulary import FactStatus, RelationType, Sensitivity, Speaker

T0 = datetime(2026, 3, 1, 12, tzinfo=UTC)
HANDLE = UUID("0192f5a0-0000-7000-8000-0000000000a1")
LINK = UUID("0192f5a0-0000-7000-8000-0000000000b2")
MODEL = "encoder-1"


def fact(value: str, *, days: int = 0, predicate: str = "contact_preference", **extra: object) -> Fact:
    at = T0 + timedelta(days=days)
    base = Fact(
        fact_id=uuid7(),
        origin_handle_id=HANDLE,
        predicate=predicate,
        value=value,
        category="contact",
        sensitivity=Sensitivity.NORMAL,
        speaker=Speaker.CUSTOMER,
        confidence=0.8,
        valid_at=at,
        created_at=at,
        status=FactStatus.ACTIVE,
        last_confirmed_at=at,
    )
    return replace(base, **extra)  # type: ignore[arg-type]


def at_similarity(similarity: float) -> tuple[list[float], list[float]]:
    """Two unit vectors whose cosine is exactly `similarity`."""
    return [1.0, 0.0], [similarity, math.sqrt(1 - similarity**2)]


def vectors(*pairs: tuple[Fact, list[float]], model: str = MODEL) -> dict[UUID, ItemVector]:
    return {f.fact_id: ItemVector(ItemKind.FACT, f.fact_id, v, model) for f, v in pairs}


def find_contradictions(*args: Any, **kwargs: Any) -> list[Relation]:
    return [p.relation() for p in band_pairs(*args, **kwargs)]


def pair(
    similarity: float, first: str, second: str, **extra: object
) -> tuple[Fact, Fact, dict[UUID, ItemVector]]:
    old, new = fact(first, **extra), fact(second, days=60, **extra)
    a, b = at_similarity(similarity)
    return old, new, vectors((old, a), (new, b))


def test_a_pair_inside_the_band_is_a_contradiction_newer_first() -> None:
    old, new, vecs = pair(0.6, "prefere ser atendido pelo gerente", "quer resolver tudo pelo aplicativo")
    assert find_contradictions([old, new], vecs) == [
        Relation(ref(new), ref(old), RelationType.CONTRADICTS, origin="band")
    ]


def test_too_far_is_another_subject_and_too_close_is_the_same_statement() -> None:
    far = pair(0.3, "prefere ser atendido pelo gerente", "tem dois filhos pequenos")
    close = pair(0.9, "prefere ser atendido pelo gerente", "prefere falar com o gerente")
    assert find_contradictions(far[:2], far[2]) == []
    assert find_contradictions(close[:2], close[2]) == []
    near_the_floor = pair(0.41, "prefere ser atendido pelo gerente", "quer resolver pelo aplicativo")
    assert len(find_contradictions(near_the_floor[:2], near_the_floor[2])) == 1


def test_near_identical_statements_whose_words_oppose_still_count() -> None:
    old, new, vecs = pair(0.93, "prefere ser atendido de manhã", "prefere ser atendido à noite")
    [found] = band_pairs([old, new], vecs)
    assert (found.newer, found.older, found.opposed) == (new, old, True)
    negated = pair(0.95, "gosta de receber ligação", "não gosta de receber ligação")
    assert len(find_contradictions(negated[:2], negated[2])) == 1


def test_opposition_reads_negations_and_closed_sets_in_three_languages() -> None:
    assert opposed("prefers email", "prefers WhatsApp")
    assert opposed("prefiere la mañana", "prefiere la noche")
    assert opposed("likes phone calls", "never answers phone calls")
    assert not opposed("prefere manhã ou tarde", "prefere tarde")
    assert not opposed("prefere atendimento no sábado", "prefere atendimento aos sábados")


def test_a_single_valued_predicate_never_enters() -> None:
    old, new, vecs = pair(0.6, "Rua A, 10", "Rua B, 20", predicate="address")
    assert find_contradictions([old, new], vecs, single_valued=lambda f: f.predicate == "address") == []


def test_a_row_of_an_unconfirmed_link_never_enters() -> None:
    old = fact("prefere ser atendido pelo gerente")
    new = fact("quer resolver tudo pelo aplicativo", days=60, origin_handle_id=LINK)
    a, b = at_similarity(0.6)
    assert find_contradictions([old, new], vectors((old, a), (new, b)), provisional={LINK}) == []
    assert len(find_contradictions([old, new], vectors((old, a), (new, b)))) == 1


def test_only_active_rows_of_the_same_slot_with_different_values_and_comparable_vectors() -> None:
    old, new, vecs = pair(0.6, "prefere ser atendido pelo gerente", "quer resolver tudo pelo aplicativo")
    retracted = replace(old, status=FactStatus.RETRACTED)
    assert find_contradictions([retracted, new], vecs) == []
    other_subject = replace(new, about_handle_id=LINK)
    assert find_contradictions([old, other_subject], vecs) == []
    other_predicate = replace(new, predicate="note")
    assert find_contradictions([old, other_predicate], vecs) == []
    same_value = replace(new, value="Prefere ser atendido pelo gerente.")
    assert find_contradictions([old, same_value], vecs) == []
    mixed = {**vecs, new.fact_id: replace(vecs[new.fact_id], model="encoder-2")}
    assert find_contradictions([old, new], mixed) == []
    without_vector = {old.fact_id: vecs[old.fact_id]}
    assert find_contradictions([old, new], without_vector) == []


def test_a_pair_already_linked_by_any_edge_is_not_drawn_again() -> None:
    old, new, vecs = pair(0.6, "prefere ser atendido pelo gerente", "quer resolver tudo pelo aplicativo")
    assert find_contradictions([old, new], vecs, related=[(old.fact_id, new.fact_id)]) == []


def test_the_cap_keeps_opposed_pairs_first_then_the_closest() -> None:
    base = fact("sem preferência declarada")
    rows = [base]
    vecs = {base.fact_id: ItemVector(ItemKind.FACT, base.fact_id, [1.0, 0.0], MODEL)}
    for i, (similarity, text) in enumerate(
        [(0.5, "quer atendimento humano"), (0.7, "quer atendimento por robô"), (0.45, "atendimento rápido")]
    ):
        row = fact(text, days=i + 1)
        rows.append(row)
        vecs[row.fact_id] = ItemVector(ItemKind.FACT, row.fact_id, at_similarity(similarity)[1], MODEL)
    found = band_pairs(rows, vecs, max_pairs=2)
    # "sem" is a negation: every pair with `base` opposes, so the closest of those come first.
    assert [p.similarity for p in found] == [0.7, 0.5]
    assert all(p.older == base for p in found)
    assert len(find_contradictions(rows, vecs, max_pairs=20)) <= 20


def test_the_same_input_gives_the_same_edges() -> None:
    old, new, vecs = pair(0.6, "prefere ser atendido pelo gerente", "quer resolver tudo pelo aplicativo")
    assert find_contradictions([old, new], vecs) == find_contradictions([new, old], vecs)
