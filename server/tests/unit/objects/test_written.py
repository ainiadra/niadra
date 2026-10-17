"""An identifier written as it is names its object, with no `key.match` declared (05/10/2026: a CNJ the
client wrote named its case only after the space declared a pattern for it)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from niadra.app.object_memory import known_objects
from niadra.app.state import _names
from niadra.config.models import SpaceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import FieldState, ObjectRead, StateRef
from niadra.domain.compile.candidates import object_name
from niadra.domain.extract.prompt import build_prompt
from niadra.domain.objects.written import distinctive, normalized, writes
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.vocabulary import Speaker
from tests.unit.extract.helpers import event, material, rules

NUMBER = "0801234-67.2026.8.26.0100"
NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
SPACE = UUID(int=7)
CASES = ObjectTypeRegistry.model_validate(
    {
        "types": [
            {
                "type": "legal_case",
                "ownership": "subject",
                "mirror_of": {"system": "cases"},
                "key": {"natural": ["case_id"]},
                "fields": {
                    "case_id": {"type": "string"},
                    "cnj": {"type": "string", "label": "Número CNJ"},
                    "status": {"type": "string"},
                    "claim_amount": {"type": "string"},
                    "assigned_lawyer": {"type": "string", "pii": True},
                },
            }
        ]
    }
)


def config() -> SpaceConfig:
    return SpaceConfig(  # type: ignore[arg-type]
        space_id=SPACE, tenant_id=SPACE, region="us-east-2", environment="sandbox", object_types=CASES
    )


def case(external_id: str, cnj: str, *, lawyer: str = "Rafael 12345", days: int = 1) -> BusinessObject:
    return BusinessObject(
        object_id=UUID(int=int(external_id.encode().hex(), 16) % 10**9),
        key=ObjectKey("legal_case", "legaldesk", external_id),
        state={
            "case_id": external_id,
            "cnj": cnj,
            "status": "ativo",
            "claim_amount": "45000.00",
            "assigned_lawyer": lawyer,
        },
        as_of=NOW - timedelta(days=days),
        source_id=UUID(int=2),
    )


@pytest.mark.parametrize(
    ("value", "names"),
    [
        (NUMBER, True),
        ("PED-12345", True),
        ("ABC1D23", True),
        ("aberto", False),
        ("10h", False),
        ("2026", False),
        ("2026-10-05", False),
        ("2026-10-05T10:00:00", False),
        ("45000.00", False),
        ("1.234,56", False),
        ("x" * 70 + "123", False),
    ],
)
def test_a_value_names_its_object_only_when_nothing_else_writes_it_by_chance(value: str, names: bool) -> None:
    assert distinctive(value) is names


def test_a_value_is_written_whole_after_case_and_spaces() -> None:
    text = normalized(f"Sobre o processo  {NUMBER}, alguma novidade?")
    assert writes(text, normalized(NUMBER))
    assert writes(normalized("pedido ped-12345."), normalized("PED-12345"))
    assert not writes(normalized("pedido PED-123456"), normalized("PED-12345"))
    assert not writes(normalized("9PED-12345"), normalized("PED-12345"))


def test_a_customer_turn_writing_a_case_number_names_that_case_with_no_pattern_declared() -> None:
    other = "0802345-86.2026.8.26.0100"
    objects = known_objects([case("c-1", NUMBER), case("c-2", other, days=2)], config())
    turns = [event(Speaker.CUSTOMER, f"sobre o processo {NUMBER}", 1)]
    p = build_prompt(replace(material(turns), objects=objects), rules(), flagged=frozenset(), spans={})
    named = {k.external_id for keys in p.aliases.written.values() for k in keys}
    assert named == {"c-1"}
    [line] = [x for x in p.user.splitlines() if "c-1" in x and x.startswith("[o")]
    assert line.endswith("(cnj written in e1)")
    assert NUMBER not in "\n".join(x for x in p.user.splitlines() if x.startswith("[o"))


def test_personal_data_amounts_and_values_two_objects_hold_name_nothing() -> None:
    twins = known_objects([case("c-1", NUMBER), case("c-2", NUMBER, days=2)], config())
    assert all(not k.keys for k in twins)
    [only] = known_objects([case("c-1", NUMBER, lawyer="Rafael 99999")], config())
    # The natural key "c-1" is too short to be written by no one else; the lawyer is personal data; the
    # claim is an amount.
    assert dict(only.keys) == {"cnj": NUMBER}


@pytest.mark.parametrize(
    ("value", "names"),
    [
        ("PED-77", True),
        ("Lote 1", True),
        ("c-1", False),
        ("10h", False),
        ("17th", False),
        ("aberto", False),
        ("2026-10-05", False),
        ("45000.00", False),
    ],
)
def test_a_natural_key_names_its_object_with_one_digit(value: str, names: bool) -> None:
    """A short order number the company declared its key named nothing."""
    assert distinctive(value, natural=True) is names
    assert not distinctive(value)


def test_a_short_natural_key_names_its_case_and_an_ordinary_field_with_the_same_shape_does_not() -> None:
    held = replace(case("PED-77", NUMBER), state={**case("PED-77", NUMBER).state, "status": "OS-45"})
    [only] = known_objects([held], config())
    assert dict(only.keys) == {"case_id": "PED-77", "cnj": NUMBER}
    names = CASES.name_fields()["legal_case"]
    short = replace(held, state={"case_id": "PED-77", "status": "OS-45"})
    assert object_name(short, frozenset(), {}, names) == "case_id PED-77"
    assert object_name(short, frozenset(), {}, (names[0], names[1], frozenset())) is None
    read = ObjectRead(
        ref=StateRef(type="legal_case", namespace="legaldesk", id="c-9"),
        state="ativo",
        fields={
            "case_id": FieldState(v="PED-77", logic="yes", status="fresh", claim_safe=False),
            "status": FieldState(v="OS-45", logic="yes", status="fresh", claim_safe=False),
        },
    )
    assert _names(CASES, [read], lambda kind: frozenset()) == {"legal_case legaldesk:c-9": "case_id PED-77"}
    ordinary = read.model_copy(update={"fields": {"status": read.fields["status"]}})
    assert _names(CASES, [ordinary], lambda kind: frozenset()) == {}
