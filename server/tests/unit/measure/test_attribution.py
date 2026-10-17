"""Attribution of outcomes (24.7): the exposure token against its spec's vectors, the four
methods, finality, a value that is never weighted, and the reconciliation with a BI file."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from niadra.app.outcomes import bi_lines
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.attribution import (
    DETERMINISTIC,
    EXPIRED,
    FINAL,
    PROBABLE,
    PROVISIONAL,
    Acted,
    Line,
    Outcome,
    Rules,
    Shown,
    TokenError,
    Touch,
    attribute,
    finality_of,
    minor_units,
    parse_token,
    reconcile,
    totals,
)
from tests.spec import cases
from tests.unit.measure.tokens import build_token

TOKEN_CASES = cases("exposure-token", {"description", "op", "exposure_id", "position", "token"})
T0 = datetime(2026, 9, 29, 12, tzinfo=UTC)
ORDER = UUID("0199a3b2-0000-7000-8000-00000000a001")
HANDLE = UUID("0199a3b2-0000-7000-8000-00000000b001")
EXPOSURE = UUID("0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e55")
OTHER = UUID("0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e56")
DRESS, BAG, BELT = "item_variant:loja:101", "item_variant:loja:102", "item_variant:loja:103"
ALL = frozenset({"line", "order", "identity", "assisted_handoff"})
RULES = Rules(
    ALL, timedelta(days=7), timedelta(days=1), timedelta(days=30), frozenset({"returned", "cancelled"})
)


@pytest.mark.parametrize("case", TOKEN_CASES, ids=[c["id"] for c in TOKEN_CASES])
def test_the_exposure_token_vectors(case: dict[str, Any]) -> None:
    expect = case["expect"]
    if case["op"] == "build":
        assert build_token(UUID(case["exposure_id"]), case["position"]) == expect["token"]
        return
    if "error" in expect:
        with pytest.raises(TokenError) as raised:
            parse_token(case["token"])
        assert raised.value.code == expect["error"]
        return
    assert parse_token(case["token"]) == (UUID(expect["exposure_id"]), expect["position"])


def test_every_token_vector_runs() -> None:
    assert len(TOKEN_CASES) == 14


def shown(**change: Any) -> Shown:
    fields: dict[str, Any] = {
        "exposure_id": str(EXPOSURE),
        "at": T0 - timedelta(hours=2),
        "positions": {1: DRESS, 2: BAG},
        "turn_id": "t-stylist-1",
        "agent": "stylist",
    }
    return Shown(**(fields | change))


def outcome(*lines: Line, **change: Any) -> Outcome:
    fields: dict[str, Any] = {
        "outcome": "purchase",
        "object_id": ORDER,
        "object_ref": "order:shop:8812",
        "handle_id": HANDLE,
        "state": "delivered",
        "finality": PROVISIONAL,
        "reached_at": T0,
        "success": True,
        "lines": lines,
        "currency": "BRL",
    }
    return Outcome(**(fields | change))


def test_a_line_with_the_token_of_the_card_is_attributed_to_that_exposure_deterministically() -> None:
    line = Line("1", DRESS, 19990, stamp=build_token(EXPOSURE, 1))
    [link] = [x for x in attribute(outcome(line), [shown()], [], RULES) if x.method == "line"]
    assert (link.method, link.band, link.exposure_id, link.position) == (
        "line",
        DETERMINISTIC,
        str(EXPOSURE),
        1,
    )
    assert (link.turn_id, link.agent, link.value_minor, link.finality) == (
        "t-stylist-1",
        "stylist",
        19990,
        PROVISIONAL,
    )
    assert link.final_at == T0 + timedelta(days=30)


def test_a_token_that_does_not_check_or_names_no_known_card_never_attributes_a_line() -> None:
    bad = [
        build_token(EXPOSURE, 7),  # a position the exposure did not have
        build_token(OTHER, 1),  # an exposure the subject was never shown
        build_token(EXPOSURE, 1)[:-1] + "A",  # edited by hand
    ]
    for stamp in bad:
        links = attribute(outcome(Line("1", None, 19990, stamp=stamp)), [shown()], [], RULES)
        assert [x.method for x in links] == [], stamp
    # An exposure inferred from what a tool returned counts more than was seen: never by line.
    inferred = shown(method="tool_result")
    links = attribute(outcome(Line("1", None, 19990, stamp=build_token(EXPOSURE, 1))), [inferred], [], RULES)
    assert links == []


def test_the_order_token_or_else_the_agents_own_action_covers_the_lines_without_one() -> None:
    lines = (Line("1", None, 19990, stamp=build_token(EXPOSURE, 1)), Line("2", None, 14990))
    found = attribute(outcome(*lines, stamp=build_token(EXPOSURE, 2)), [shown()], [], RULES)
    assert [(x.line_ref, x.method, x.position) for x in found] == [("1", "line", 1), ("2", "order", 2)]
    action = Acted(UUID(int=7), T0 - timedelta(hours=1), "closing")
    later = Acted(UUID(int=8), T0 + timedelta(hours=1), "closing")  # after the outcome: not its origin
    found = attribute(outcome(Line("2", None, 14990), actions=(action, later)), [], [], RULES)
    assert [(x.method, x.band, x.action_id, x.agent) for x in found] == [
        ("order", DETERMINISTIC, UUID(int=7), "closing")
    ]


def test_identity_is_probable_and_kept_even_for_a_line_a_token_attributes() -> None:
    click = Touch(BAG, T0 - timedelta(days=3), str(OTHER), "t-voice-2", "voice")
    lines = (
        Line("1", DRESS, 19990, stamp=build_token(EXPOSURE, 1)),
        Line("2", BAG, 14990),
        Line("3", BELT, 5990),
    )
    found = attribute(outcome(*lines), [shown()], [click], RULES)
    by_line = {(x.line_ref, x.method): x for x in found}
    assert set(by_line) == {("1", "line"), ("1", "identity"), ("2", "identity")}
    # The click within 7 days wins over the exposure within a day; the dress was only shown, 2 hours before.
    assert (by_line[("2", "identity")].exposure_id, by_line[("2", "identity")].agent) == (str(OTHER), "voice")
    assert by_line[("1", "identity")].exposure_id == str(EXPOSURE)
    assert all(x.band == PROBABLE for (_, method), x in by_line.items() if method == "identity")
    # Past the windows, the belt was never engaged nor shown: no link at all.
    old = Touch(BAG, T0 - timedelta(days=8))
    assert [x.method for x in attribute(outcome(Line("2", BAG, 14990)), [], [old], RULES)] == []


def test_an_identity_link_carries_the_calibrated_confidence_and_a_token_link_none() -> None:
    lines = (Line("1", DRESS, 19990, stamp=build_token(EXPOSURE, 1)),)
    calibrated = replace(RULES, identity_confidence=0.82)
    found = {x.method: x.confidence for x in attribute(outcome(*lines), [shown()], [], calibrated)}
    assert found == {"line": None, "identity": 0.82}
    assert {x.confidence for x in attribute(outcome(*lines), [shown()], [], RULES)} == {None}


def test_attribution_never_sums_weights_as_money() -> None:
    # A line attributed by a token and by identity keeps its value whole in each method; a
    # probable link is never `confidence x value`, and no total adds a probable value to a deterministic one.
    lines = (Line("1", DRESS, 30000, stamp=build_token(EXPOSURE, 1)),)
    handoff = uuid7(int((T0 - timedelta(days=1)).timestamp() * 1000))
    found = attribute(outcome(*lines, handoff_id=handoff), [shown()], [], RULES)
    assert sorted((x.method, x.value_minor) for x in found) == [
        ("assisted_handoff", 30000), ("identity", 30000), ("line", 30000)
    ]  # fmt: skip
    assert all(x.confidence is None or 0 <= x.confidence <= 1 for x in found)
    rows = [
        {"outcome": x.outcome, "method": x.method, "band": x.band, "finality": x.finality,
         "currency": x.currency, "outcomes": 1, "lines": 1, "value_minor": x.value_minor}
        for x in found
    ]  # fmt: skip
    summed = totals(rows)
    assert {(t.method, t.band, t.value_minor) for t in summed} == {
        ("line", DETERMINISTIC, 30000), ("identity", PROBABLE, 30000), ("assisted_handoff", PROBABLE, 30000)
    }  # fmt: skip
    # One total per method and band: nothing adds the three up, and nothing is 0.5 x 300.
    assert len(summed) == 3
    assert all(t.value_minor in (0, 30000) for t in summed)


def test_a_returned_or_cancelled_line_is_final_and_worth_nothing() -> None:
    lines = (
        Line("1", DRESS, 19990, stamp=build_token(EXPOSURE, 1)),
        Line("2", BAG, 14990, state="returned", stamp=build_token(EXPOSURE, 2)),
    )
    found = {x.line_ref: x for x in attribute(outcome(*lines), [shown()], [], RULES) if x.method == "line"}
    assert (found["1"].finality, found["1"].value_minor) == (PROVISIONAL, 19990)
    assert (found["2"].finality, found["2"].value_minor, found["2"].state) == (FINAL, None, "returned")
    assert found["2"].final_at is None


def test_the_types_lifecycle_says_which_state_is_provisional_and_which_final() -> None:
    assert finality_of("delivered", ["delivered"], ["kept", "returned"]) == PROVISIONAL
    assert finality_of("kept", ["delivered"], ["kept", "returned"]) == FINAL
    assert finality_of("shipped", ["delivered"], ["kept", "returned"]) is None


def test_an_object_past_its_deadline_is_attributed_as_expired_and_worth_nothing() -> None:
    lines = (Line("1", DRESS, 19990, stamp=build_token(EXPOSURE, 1)),)
    late = outcome(*lines, state=EXPIRED, finality=EXPIRED, success=False)
    [link] = [x for x in attribute(late, [shown()], [], RULES) if x.method == "line"]
    assert (link.finality, link.value_minor, link.currency, link.final_at) == (EXPIRED, None, None, None)


def test_money_in_minor_units_by_currency() -> None:
    assert minor_units("199.90", "BRL") == 19990
    assert minor_units(199.9, "BRL") == 19990
    assert minor_units("0.125", "USD") == 12  # half to even
    assert minor_units("1500", "JPY") == 1500
    assert minor_units(True, "BRL") is None
    assert minor_units("12,50", "BRL") is None


def test_reconciliation_with_a_sample_bi_file() -> None:
    # The period's attributed lines against the company's BI export, line by line.
    bi = bi_lines((Path(__file__).parent / "fixtures" / "bi-sample.csv").read_text().splitlines())
    ours = {
        ("e2e-8812", "1"): 19990,
        ("e2e-8812", "2"): 14990,
        ("e2e-8813", "1"): 9090,  # the coupon prorated another way: R$ 1,00 apart
        ("e2e-8814", "1"): 31000,
        ("e2e-8816", "1"): 4990,  # an order the BI does not attribute
    }
    found = reconcile(ours, bi, tolerance=0.02)
    assert (found.matched, found.only_ours, found.only_bi) == (4, 1, 1)
    assert (found.ours_minor, found.bi_minor, found.currency) == (75070, 74970, "BRL")
    assert found.deviation == round(100 / 74970, 6)
    assert found.within
    assert found.worst == (("e2e-8813", "1", 9090, 8990),)
    assert not reconcile(ours, bi, tolerance=0.001).within


def test_a_bi_file_without_its_columns_or_with_a_broken_line_is_refused() -> None:
    with pytest.raises(InvalidInput, match="lacks the columns currency"):
        bi_lines(["order,line,value", "1,1,10.00"])
    with pytest.raises(InvalidInput, match="line 3"):
        bi_lines(["order,line,value,currency", "1,1,10.00,BRL", "2,1,dez,BRL"])


def test_a_handoff_assists_only_the_outcomes_within_its_window() -> None:
    lines = (Line("1", DRESS, 30000),)

    def assisted(handoff: UUID, rules: Rules = RULES) -> list[int | None]:
        found = attribute(outcome(*lines, handoff_id=handoff), [], [], rules)
        return [x.value_minor for x in found if x.method == "assisted_handoff"]

    def minted(before: timedelta) -> UUID:
        return uuid7(int((T0 - before).timestamp() * 1000))

    assert assisted(minted(timedelta(days=6))) == [30000]
    assert assisted(minted(timedelta(days=8))) == []
    assert assisted(minted(timedelta(days=8)), replace(RULES, handoff_window=timedelta(days=30))) == [30000]
    # A handoff after the outcome did not assist it, and an id Niadra did not mint carries no time.
    assert assisted(minted(-timedelta(hours=1))) == []
    assert assisted(UUID("6f1c2a4e-8b3d-4c1e-9a7f-2d5e8b1c0a93")) == []
