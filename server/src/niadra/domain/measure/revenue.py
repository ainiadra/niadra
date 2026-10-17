"""The signed revenue definition (24.7): what an order and each of its lines are worth, the
way the company's BI counts them, so the attributed number matches its own.

Pure, in the currency's minor units. A line's `value` is its amount before discounts, coupons, shipping and
taxes, and each of those comes in apart: the line's own discount and tax, and the order's coupon, shipping,
discount and tax. The definition says which of them a value takes in:

- `basis`: `net_of_discount` takes the discounts (the line's own, and the coupon as `coupons` says) off;
  `gross` takes none of them off.
- `coupons`: `prorated_by_line` spreads the order's coupon over its lines in proportion to their value;
  `order_level` leaves the lines whole and takes the coupon off the order's own value only.
- `shipping` and `taxes`: `excluded` leaves them out; `included` spreads the order's shipping over its lines
  like a prorated coupon, and adds each line's own tax.
- `cancellations`: `by_line` makes a line in a failed state (cancelled, returned) worth nothing; `whole_order`
  reads no line's state, so only the order's own state takes its value.
- `exchanges`: a line that names the line it replaced is `new_item` a line of its own, while the replaced one
  follows its own state; or `same_item` the same sale, so the replaced line keeps its value and attribution
  and the replacing line counts nothing.
- `split_orders`: `by_seller` counts each seller's part of a split purchase as its own order; `whole_order`
  counts the parts of one purchase as one order.

A replacing line never shares the order's coupon or shipping: they were spread when the order was placed.
"""

from collections.abc import Sequence
from dataclasses import dataclass

NET = "net_of_discount"
PRORATED = "prorated_by_line"
INCLUDED = "included"
BY_LINE = "by_line"
SAME_ITEM = "same_item"
WHOLE_ORDER = "whole_order"


@dataclass(frozen=True, slots=True)
class Revenue:
    """The definition's settings, as `config.measurement.RevenueDefinition` names them."""

    basis: str = NET
    coupons: str = PRORATED
    shipping: str = "excluded"
    taxes: str = "excluded"
    cancellations: str = BY_LINE
    exchanges: str = "new_item"
    split_orders: str = "by_seller"

    @property
    def net(self) -> bool:
        return self.basis == NET


DEFAULT = Revenue()


@dataclass(frozen=True, slots=True)
class Priced:
    """A line's money as the order's system reports it: its amount, its own discount and its tax."""

    value: int | None
    discount: int = 0
    tax: int = 0
    replacing: bool = False
    """It names a line of the order it replaced, in an exchange."""


@dataclass(frozen=True, slots=True)
class OrderAmounts:
    """The order's own money: its amount (for an order without lines), and what applies to it whole."""

    value: int | None = None
    discount: int = 0
    coupon: int = 0
    shipping: int = 0
    tax: int = 0


def line_values(lines: Sequence[Priced], order: OrderAmounts, revenue: Revenue) -> list[int | None]:
    """Each line's value under the definition; None for a line without an amount."""
    base = [_base(line, revenue) for line in lines]
    shared = [i for i, line in enumerate(lines) if not line.replacing and base[i] is not None]
    weights = [base[i] or 0 for i in shared]
    coupon = [0] * len(lines)
    if revenue.net and revenue.coupons == PRORATED:
        for i, share in zip(shared, prorate(min(order.coupon, sum(weights)), weights), strict=True):
            coupon[i] = share
    shipping = [0] * len(lines)
    if revenue.shipping == INCLUDED:
        for i, share in zip(shared, prorate(order.shipping, weights), strict=True):
            shipping[i] = share
    out: list[int | None] = []
    for i, line in enumerate(lines):
        value = base[i]
        if value is not None:
            value += shipping[i] - coupon[i] + (line.tax if revenue.taxes == INCLUDED else 0)
        out.append(value)
    return out


def order_value(order: OrderAmounts, revenue: Revenue) -> int | None:
    """An order without lines: its amount under the definition."""
    if order.value is None:
        return None
    value = order.value
    if revenue.net:
        value = max(0, value - order.discount - order.coupon)
    if revenue.shipping == INCLUDED:
        value += order.shipping
    if revenue.taxes == INCLUDED:
        value += order.tax
    return value


def order_coupon(order: OrderAmounts, revenue: Revenue) -> int:
    """What the order as a whole takes off the sum of its lines: the coupon kept at the order's level."""
    return order.coupon if revenue.net and revenue.coupons != PRORATED else 0


def prorate(total: int, weights: Sequence[int]) -> list[int]:
    """`total` split in proportion to `weights`, in whole minor units that add up to it exactly: the
    largest remainders take the units left, the earlier line first on a tie. Equal parts when no weight is
    positive."""
    if not weights or total <= 0:
        return [0] * len(weights)
    positive = [max(0, w) for w in weights]
    if not sum(positive):
        positive = [1] * len(weights)
    whole = sum(positive)
    shares = [total * w // whole for w in positive]
    order = sorted(range(len(positive)), key=lambda i: (-(total * positive[i] % whole), i))
    for i in order[: total - sum(shares)]:
        shares[i] += 1
    return shares


def _base(line: Priced, revenue: Revenue) -> int | None:
    if line.value is None:
        return None
    return max(0, line.value - line.discount) if revenue.net else line.value
