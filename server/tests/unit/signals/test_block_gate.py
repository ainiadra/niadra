"""The constraints block beside a pack passes each entry by what it names: a field of a type the reader may
not read, or one its type holds private, leaves with every conflict and relaxation step that names it, and
the block is versioned again."""

from niadra.domain.compile.constraints import gated
from niadra.domain.signals.render import version

BLOCK = {
    "subject": {"for": "self"},
    "hard": [
        {"id": "h1", "attr": "health_plan.copay", "op": "eq", "values": [False], "source": "stated"},
        {"id": "h2", "attr": "exam.urgent", "op": "eq", "values": [True], "source": "stated"},
    ],
    "soft": [{"id": "s1", "attr": "exam.lab", "value": "central", "polarity": "prefer", "weight": 1.0}],
    "attributes": [
        {"id": "z1", "name": "size", "value": "M", "source": "stated", "apply": "always"},
        {"id": "z2", "name": "blood_type", "value": "O+", "source": "inferred", "apply": "when_asked"},
    ],
    "exclude": ["exam:lab:x-1", "health_plan:operadora:basic"],
    "already_presented": [
        {"ref": "exam:lab:x-2", "times": 1},
        {"ref": "health_plan:operadora:pleno", "times": 1},
    ],
    "relaxation_order": ["soft", "z2"],
    "conflicts": [{"ids": ["h2", "s1"], "kept": "h2", "by": "precedence"}],
}


def refused(path: str) -> bool:
    return not (path.startswith("exam") or path == "blood_type")


def test_an_entry_the_gate_refuses_leaves_with_what_names_it() -> None:
    block = {**BLOCK, "version": version(BLOCK)}
    kept = gated(block, refused)
    assert [h["id"] for h in kept["hard"]] == ["h1"]
    assert "soft" not in kept
    assert [a["id"] for a in kept["attributes"]] == ["z1"]
    assert kept["exclude"] == ["health_plan:operadora:basic"]
    assert [p["ref"] for p in kept["already_presented"]] == ["health_plan:operadora:pleno"]
    assert "conflicts" not in kept
    assert "relaxation_order" not in kept
    assert kept["version"] == version(kept) != block["version"]


def test_a_gate_that_refuses_nothing_keeps_the_same_version() -> None:
    block = {**BLOCK, "version": version(BLOCK)}
    assert gated(block, lambda _: True) == block


def test_an_option_shown_keeps_only_the_numbers_of_fields_the_gate_lets_go() -> None:
    shown = {
        "ref": "health_plan:operadora:pleno",
        "times": 1,
        "values": {
            "monthly_price": {"v": 690.0, "role": "price_full", "claim_safe": True},
            "broker_fee": {"v": 40.0, "claim_safe": False},
        },
    }
    block = {"subject": {"for": "self"}, "already_presented": [shown]}
    kept = gated({**block, "version": version(block)}, lambda path: path != "health_plan.broker_fee")
    price = shown["values"]["monthly_price"]
    assert kept["already_presented"] == [{**shown, "values": {"monthly_price": price}}]
    only_the_ref = gated({**block, "version": version(block)}, lambda path: "." not in path)
    assert only_the_ref["already_presented"] == [{"ref": "health_plan:operadora:pleno", "times": 1}]
