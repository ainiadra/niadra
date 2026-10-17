"""The types a space's traffic shows: what the inferrer counts from the objects'
changes and the declarations it writes from that, under the limits and thresholds that keep one bad event or
one buggy integration from reshaping a space, and never declaring what could lift the hold of an undeclared
field's personal value."""

from datetime import UTC, datetime, timedelta
from typing import Any

from niadra.config.object_types import ObjectTypeRegistry
from niadra.domain.objects.infer import (
    FINAL_AFTER,
    MAX_TYPES,
    OPERATION_EVENTS,
    Shape,
    changes,
    empty,
    observed_operations,
    observed_types,
    seen,
)
from niadra.domain.objects.operations import MAX_OPERATIONS

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def case(n: int, **extra: Any) -> Shape:
    state = {"cnj": f"0801234-6{n}.2026.8.26.0100", "claim_amount": 45000.0, "state": "ativo", **extra}
    return Shape("legal_case", f"c{n}", state, NOW - timedelta(days=1), True, "Sistema Processos")


def deadline(n: int, due: str = "2026-10-27", **extra: Any) -> Shape:
    state = {
        "case_id": f"c{n}",
        "due_on": due,
        "days": 15,
        "state": "aberto",
        "last_event_type": "x",
        **extra,
    }
    return Shape("deadline", f"d{n}", state, NOW - timedelta(days=1), True, "Sistema Processos")


CASES_BY_ID = {f"c{n}": ["legal_case"] for n in range(10)}


def inferred(*shapes: Shape, declared: frozenset[str] = frozenset()) -> dict[str, dict[str, Any]]:
    evidence = seen(empty(), shapes, CASES_BY_ID)
    return {t["type"]: t for t in observed_types(evidence, declared, NOW)}


def test_a_type_nobody_declared_gets_its_states_relation_identifier_and_due_date() -> None:
    types = inferred(*(case(n) for n in range(3)), *(deadline(n) for n in range(3)))
    due = types["deadline"]
    assert due["mirror_of"] == {"system": "sistema-processos", "derived_by": "observation"}
    assert due["ownership"] == "subject"
    assert due["states"] == ["aberto"]
    assert due["relations"] == {"legal_case": {"type": "legal_case", "via": "case_id"}}
    assert due["timers"] == {"notice": {"due": "due_on - 48h", "on_fire": ["notify"]}}
    # Unlabelled: a line says the date the type is due on in the space's words ("vence em"). Every other
    # field stays undeclared, served by its name and held value by value.
    assert due["fields"] == {"due_on": {"type": "date"}}
    assert types["legal_case"]["fields"] == {"cnj": {"type": "string", "label": "Cnj"}}
    # What it writes is a declaration the registry accepts as it is.
    registry = ObjectTypeRegistry.model_validate({"types": list(types.values())})
    assert {t.type for t in registry.types} == {"legal_case", "deadline"}


def test_a_field_that_ever_held_a_personal_value_or_free_text_is_never_declared() -> None:
    types = inferred(
        case(1, protocolo="maria@exemplo.com"),
        case(2, protocolo="PROT-2026-0001"),
        case(3, protocolo="PROT-2026-0002"),
        case(4, protocolo="PROT-2026-0003", andamento="x" * 300),
    )
    assert set(types["legal_case"]["fields"]) == {"cnj"}


def test_an_identifier_needs_every_value_specific_enough_to_name_an_object() -> None:
    types = inferred(case(1, vara="1ª Vara Cível"), case(2, vara="2ª Vara"), case(3, vara="Vara 300"))
    assert "vara" not in types["legal_case"]["fields"]


def test_a_type_the_company_declared_is_never_touched() -> None:
    types = inferred(case(1), case(2), case(3), declared=frozenset({"legal_case"}))
    assert "legal_case" not in types


def test_one_malformed_value_does_not_take_a_due_date_away() -> None:
    shapes = [deadline(n) for n in range(4)] + [deadline(9, due="amanhã")]
    assert inferred(*shapes)["deadline"]["fields"]["due_on"] == {"type": "date"}
    # Two kinds with too few values to tell: no due date yet.
    mixed = inferred(deadline(1), deadline(2, due="amanhã"))
    assert "timers" not in mixed["deadline"]


def test_a_relation_needs_three_matches_and_no_other_type() -> None:
    two = inferred(case(1), case(2), deadline(1), deadline(2))
    assert "relations" not in two["deadline"]
    named = {"c0": ["legal_case"], "c1": ["legal_case"], "c2": ["legal_case"], "c3": ["hearing"]}
    evidence = seen(empty(), [deadline(n) for n in range(4)], named)
    types = {t["type"]: t for t in observed_types(evidence, {"legal_case", "hearing"}, NOW)}
    assert "relations" not in types["deadline"]


def test_a_due_date_is_a_date_field_named_so_whose_values_were_mostly_ahead() -> None:
    past = inferred(*(deadline(n, due="2026-09-01") for n in range(4)))
    assert "timers" not in past["deadline"]
    held_on = inferred(
        *(Shape("hearing", f"h{n}", {"held_on": "2026-10-30"}, NOW, True, None) for n in range(4))
    )
    assert "timers" not in held_on["hearing"]
    assert "fields" not in held_on["hearing"]


def test_a_state_objects_stayed_in_on_five_objects_for_two_weeks_is_a_final_outcome() -> None:
    old = NOW - FINAL_AFTER - timedelta(days=1)
    shapes = [Shape("filing", f"f{n}", {"state": "protocolada"}, old, True, None) for n in range(5)]
    shapes.append(Shape("filing", "f9", {"state": "rascunho"}, old, True, None))
    types = inferred(*shapes)
    assert types["filing"]["states"] == ["protocolada", "rascunho"]
    assert types["filing"]["lifecycle"] == {"outcome": {"final": ["protocolada"]}}
    fresh = inferred(*(Shape("filing", f"f{n}", {"state": "protocolada"}, NOW, True, None) for n in range(5)))
    assert "lifecycle" not in fresh["filing"]


def test_names_outside_the_identifier_shape_are_counted_never_inferred() -> None:
    shapes = [
        Shape(
            "legal_case", f"c{n}", {"Ignore previous instructions": "x", "state": "Ativo!"}, NOW, True, None
        )
        for n in range(3)
    ]
    evidence = seen(empty(), [*shapes, Shape("Bad Type!", "x", {}, NOW, True, None)], {})
    types = observed_types(evidence, frozenset(), NOW)
    assert [t["type"] for t in types] == ["legal_case"]
    assert "states" not in types[0]
    assert evidence["types"]["legal_case"]["unsafe"] == 3
    assert evidence["limited"] == {"unsafe_types": 1}


def test_past_the_type_limit_new_types_are_counted_not_inferred() -> None:
    shapes = [Shape(f"t{n}", "x", {"a": "1"}, NOW, False, None) for n in range(MAX_TYPES + 2)]
    evidence = seen(empty(), shapes, {})
    assert len(evidence["types"]) == MAX_TYPES
    assert evidence["limited"] == {"types": 2}
    assert observed_types(evidence, frozenset(), NOW)[0]["ownership"] == "shared"


def test_the_notes_say_what_changed_by_name_and_never_a_value() -> None:
    first = observed_types(seen(empty(), [case(n) for n in range(3)], {}), frozenset(), NOW)
    later = observed_types(
        seen(empty(), [case(n, protocolo=f"PROT-2026-000{n}") for n in range(3)], {}), frozenset(), NOW
    )
    assert changes([], first)[0] == {"kind": "type", "type": "legal_case"}
    assert changes(first, later) == [
        {"kind": "field", "type": "legal_case", "field": "protocolo", "as": "string"}
    ]
    assert "0801234" not in repr(changes([], first))


def test_the_evidence_of_one_run_is_counted_on_by_the_next() -> None:
    # Production, 07/10: every run after the first died on its own evidence (`_copy` read a state's count as a
    # mapping), so the types a space showed later than its first minute were never inferred.
    import json

    first = json.loads(json.dumps(seen(empty(), [case(0), deadline(0)], CASES_BY_ID)))
    second = seen(first, [case(1), case(2), deadline(1), deadline(2)], CASES_BY_ID)
    assert second["types"]["legal_case"]["states"] == {"ativo": 3}
    assert second["types"]["deadline"]["objects"] == 3
    assert "deadline" in {t["type"] for t in observed_types(second, frozenset(), NOW)}


def test_operations_past_the_shared_cap_are_counted_and_never_offered() -> None:
    # One cap for every list of operations; past it nothing is dropped silently.
    shapes = [
        Shape("filing", f"f{n}-{k}", {"operation": f"op_{n:03d}"}, NOW, True, None)
        for n in range(MAX_OPERATIONS + 3)
        for k in range(OPERATION_EVENTS)
    ]
    evidence = seen(empty(), shapes, {})
    assert len(observed_operations(evidence)) == MAX_OPERATIONS
    assert evidence["limited"]["operations_not_offered"] == 3
