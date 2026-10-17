"""The signed revenue definition (24.7), one setting at a time: what each line and the order
are worth, so the attributed number matches the company's BI. An order of a dress (R$ 199,90, R$ 20,00 off)
and a bag (R$ 100,00), with a R$ 30,00 coupon, R$ 15,00 of shipping and the lines' taxes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.app.outcomes import outcome_of, rules_of
from niadra.config.measurement import MeasurementSettings
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.attribution import (
    FINAL,
    PROVISIONAL,
    BiLine,
    Line,
    Link,
    Outcome,
    Rules,
    Shown,
    attribute,
    reconcile,
)
from niadra.domain.measure.revenue import OrderAmounts, Priced, Revenue, line_values, prorate
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.vocabulary import Feature
from niadra.testing.cell import sample_config
from tests.unit.measure.tokens import build_token

T0 = datetime(2026, 9, 29, 12, tzinfo=UTC)
EXPOSURE = "0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e55"
ORDER = UUID("0199a3b2-0000-7000-8000-00000000a001")
HANDLE = UUID("0199a3b2-0000-7000-8000-00000000b001")
DRESS, BAG = "item_variant:loja:101", "item_variant:loja:102"
FAILED = frozenset({"returned", "cancelled"})


def rules(**revenue: str) -> Rules:
    return Rules(
        frozenset({"order", "assisted_handoff"}), timedelta(days=7), timedelta(days=1), timedelta(days=30),
        FAILED, revenue=Revenue(**revenue),
    )  # fmt: skip


def order(*lines: Line, **change: Any) -> Outcome:
    fields: dict[str, Any] = {
        "outcome": "purchase", "object_id": ORDER, "object_ref": "order:shop:8812", "handle_id": HANDLE,
        "state": "delivered", "finality": PROVISIONAL, "reached_at": T0, "success": True,
        "lines": lines or LINES, "currency": "BRL", "stamp": None, "coupon_minor": 3000,
        "shipping_minor": 1500, "actions": (),
    }  # fmt: skip
    return Outcome(**(fields | change))


LINES = (
    Line("1", DRESS, 19990, discount_minor=2000, tax_minor=1800),
    Line("2", BAG, 10000, tax_minor=900),
)


def by_line(outcome: Outcome, rules: Rules) -> dict[str | None, Link]:
    """The order's links by line, with the order's own token so every line has one."""
    seen = Shown(EXPOSURE, T0 - timedelta(hours=1), {1: DRESS}, "t-1", "stylist")
    found = attribute(replace(outcome, stamp=build_token(UUID(EXPOSURE), 1)), [seen], [], rules)
    return {x.line_ref if x.method == "order" else x.method: x for x in found}


def values(outcome: Outcome, rules: Rules) -> dict[str | None, int | None]:
    return {ref: link.value_minor for ref, link in by_line(outcome, rules).items()}


def test_prorating_splits_to_the_unit_and_adds_up_exactly() -> None:
    assert prorate(1000, [1, 1, 1]) == [334, 333, 333]
    assert prorate(3000, [17990, 10000]) == [1928, 1072]
    assert sum(prorate(997, [5, 3, 0, 11])) == 997
    assert prorate(10, [0, 0]) == [5, 5]
    assert prorate(0, [3, 4]) == [0, 0]


def test_basis_net_of_discount_takes_the_lines_discount_and_the_coupon_off_and_gross_neither() -> None:
    # Net: 199,90 - 20,00 = 179,90 and 100,00 share the 30,00 coupon by value: 19,28 and 10,72.
    assert values(order(), rules()) == {"1": 17990 - 1928, "2": 10000 - 1072}
    assert values(order(), rules(basis="gross")) == {"1": 19990, "2": 10000}


def test_coupons_prorated_by_line_or_kept_at_the_orders_level() -> None:
    assert values(order(), rules(coupons="order_level")) == {"1": 17990, "2": 10000}
    # The order as a whole (a handoff that assisted it) takes the coupon off the lines' sum.
    assisted = order(handoff_id=uuid7(int((T0 - timedelta(hours=1)).timestamp() * 1000)))
    prorated = by_line(assisted, rules())["assisted_handoff"].value_minor
    kept = by_line(assisted, rules(coupons="order_level"))["assisted_handoff"].value_minor
    assert prorated == kept == 17990 + 10000 - 3000


def test_shipping_is_left_out_or_spread_over_the_lines() -> None:
    net = {"1": 17990 - 1928, "2": 10000 - 1072}
    found = values(order(), rules(shipping="included"))
    # 15,00 by the same weights: 9,64 and 5,36.
    assert (found["1"], found["2"]) == (net["1"] + 964, net["2"] + 536)
    assert sum(prorate(1500, [17990, 10000])) == 1500


def test_taxes_are_left_out_or_each_lines_own_added() -> None:
    found = values(order(), rules(taxes="included"))
    assert (found["1"], found["2"]) == (17990 - 1928 + 1800, 10000 - 1072 + 900)
    # An order without lines takes its own tax.
    whole = order(Line(""), lines=(), value_minor=29990, tax_minor=2700, coupon_minor=0, shipping_minor=0)
    assert by_line(whole, rules(taxes="included"))[None].value_minor == 29990 + 2700
    assert by_line(whole, rules())[None].value_minor == 29990


def test_cancellations_by_line_or_only_by_the_whole_orders_state() -> None:
    lines = (LINES[0], replace(LINES[1], state="cancelled"))
    line_by_line = by_line(order(*lines), rules())
    assert (line_by_line["2"].value_minor, line_by_line["2"].finality, line_by_line["2"].state) == (
        None, FINAL, "cancelled",
    )  # fmt: skip
    # The coupon was spread when the order was placed: the cancelled line's share goes with it.
    assert line_by_line["1"].value_minor == 17990 - 1928
    whole = by_line(order(*lines), rules(cancellations="whole_order"))
    assert (whole["2"].value_minor, whole["2"].finality) == (10000 - 1072, PROVISIONAL)
    # The order's own cancellation still takes everything.
    gone = by_line(
        order(*lines, state="cancelled", success=False, finality=FINAL), rules(cancellations="whole_order")
    )
    assert [gone[ref].value_minor for ref in ("1", "2")] == [None, None]


def test_exchanges_as_a_new_item_or_as_the_same_sale() -> None:
    returned = replace(LINES[1], state="returned")
    swap = Line("3", "item_variant:loja:103", 10000, replaces="2")
    new_item = values(order(LINES[0], returned, swap), rules())
    # The replacing line shares neither the coupon nor the shipping: they were spread before the exchange.
    assert new_item == {"1": 17990 - 1928, "2": None, "3": 10000}
    same_item = values(order(LINES[0], returned, swap), rules(exchanges="same_item"))
    assert same_item == {"1": 17990 - 1928, "2": 10000 - 1072}


def test_split_orders_count_each_sellers_part_or_the_whole_purchase() -> None:
    part = order(purchase="order:shop:P-77")
    assert {x.order_ref for x in by_line(part, rules()).values()} == {None}
    assert {x.order_ref for x in by_line(part, rules(split_orders="whole_order")).values()} == {
        "order:shop:P-77"
    }
    # The BI knows the purchase, not its parts' lines: compared order by order.
    ours = {("P-77", "1"): 16062, ("P-77", "2"): 8928, ("P-78", "1"): 5000}
    bi = [
        BiLine("P-77", "a", 20000, "BRL"),
        BiLine("P-77", "b", 4990, "BRL"),
        BiLine("P-78", "", 5000, "BRL"),
    ]
    whole = reconcile(ours, bi, 0.02, whole_orders=True)
    assert (whole.matched, whole.only_ours, whole.only_bi, whole.deviation) == (2, 0, 0, 0.0)
    assert reconcile(ours, bi, 0.02).matched == 0


def test_a_line_without_an_amount_has_no_value_and_takes_no_share() -> None:
    found = line_values([Priced(None), Priced(10000)], OrderAmounts(coupon=1000), Revenue())
    assert found == [None, 9000]


def test_the_definition_reaches_the_attribution_from_the_measurement_document() -> None:
    measurement = MeasurementSettings.model_validate(
        {
            "revenue": {
                "currency": "BRL", "coupons": "order_level", "taxes": "included",
                "split_orders": "whole_order",
            },
            "outcomes": [{
                "name": "purchase", "object_type": "order", "success_states": ["delivered"],
                "lines": {"value": "amount"}, "coupon_field": "coupon", "purchase_field": "purchase_id",
            }],
        }
    )  # fmt: skip
    config = sample_config().model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset({Feature.MEASUREMENT})),
            "object_types": ObjectTypeRegistry(),
            "measurement": measurement,
        }
    )
    state = {
        "state": "delivered", "coupon": "30.00", "purchase_id": "P-77",
        "lines": [{"line_id": "1", "ref": DRESS, "amount": "199.90", "discount": "20.00", "tax": "18.00"}],
    }  # fmt: skip
    obj = BusinessObject(
        object_id=ORDER, key=ObjectKey("order", "shop", "8812-01"), state=state, as_of=T0,
        source_id=UUID(int=1), origin_handle_id=HANDLE,
    )  # fmt: skip
    definition = measurement.outcomes[0]
    found = outcome_of(obj, definition, None, config, (), T0)
    assert (found.coupon_minor, found.purchase) == (3000, "order:shop:P-77")
    assert (found.lines[0].discount_minor, found.lines[0].tax_minor) == (2000, 1800)
    applied = rules_of(config, definition)
    assert applied.revenue == Revenue(coupons="order_level", taxes="included", split_orders="whole_order")
    link = by_line(found, replace(applied, methods=frozenset({"order"})))["1"]
    # The coupon stays at the order's level and the line's tax comes in: 199,90 - 20,00 + 18,00.
    assert (link.value_minor, link.order_ref) == (19790, "order:shop:P-77")
