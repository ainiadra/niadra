"""The constraints block rendered for a tool against the `constraint-render` vectors."""

from typing import Any

import pytest

from niadra.contracts.signals import ConstraintsBlock
from niadra.domain.signals.render import Binding, BindingArg, Call, honored, render
from tests.spec import cases

RENDER = cases("constraint-render", {"block", "binding", "families", "call", "mode", "results"})


def _binding(raw: dict[str, Any], families: dict[str, str]) -> Binding:
    assert set(raw) <= {"tool", "args", "overfetch"}
    args = []
    for arg in raw["args"]:
        assert set(arg) <= {"attr", "param", "transform", "negation", "ops"}
        negation = arg.get("negation")
        args.append(
            BindingArg(
                arg["attr"],
                arg["param"],
                arg.get("transform"),
                negation["param"] if negation else None,
                tuple(arg.get("ops", ())),
                families.get(arg["attr"]),
            )
        )
    return Binding(raw["tool"], tuple(args), raw.get("overfetch", False))


@pytest.mark.parametrize("case", RENDER, ids=lambda c: c["id"])
def test_the_block_renders_as_the_vector_says(case: dict[str, Any]) -> None:
    block = ConstraintsBlock.model_validate({"text": "", **case["block"]})
    raw_call = case["call"]
    assert set(raw_call) <= {"args", "for", "category", "asked"}
    call = Call(
        raw_call["args"], raw_call["for"], raw_call.get("category"), frozenset(raw_call.get("asked", ()))
    )
    got = render(block, _binding(case["binding"], case["families"]), call, case["mode"])
    expect = case["expect"]
    assert got.applies == expect["applies"]
    assert dict(got.args) == expect["args"]
    assert dict(got.suggested) == expect["suggested"]
    assert list(got.injected) == expect["injected"]
    assert list(got.hard_sent) == expect["hard_sent"]
    assert list(got.residual) == expect["residual"]
    assert list(got.post_filter) == expect["post_filter"]
    assert [{"id": i, "param": p} for i, p in got.conflicts] == expect["conflicts"]
    if "results" in case:
        seen = honored(block, got.hard_sent, case["results"])
        assert {
            "results_checked": seen.results_checked,
            "violations": seen.violations,
            "unverifiable": seen.unverifiable,
        } == expect["honored"]
    else:
        assert "honored" not in expect


def test_an_inferred_size_is_never_injected_whatever_the_mode() -> None:
    for case in RENDER:
        block = ConstraintsBlock.model_validate({"text": "", **case["block"]})
        inferred = {a.id for a in block.attributes if a.source not in ("stated", "correction")}
        assert not inferred & set(case["expect"]["injected"]), case["id"]
