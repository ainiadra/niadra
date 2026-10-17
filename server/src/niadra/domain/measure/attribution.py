"""Outcomes and their attribution (24.7). Pure: the outcome, what the subject was shown and
engaged with, and the rules go in; the links come out.

An outcome is an object of a measured type in a state its type's lifecycle calls provisional (`delivered`) or
final (`kept`, `returned`, `cancelled`), or past its deadline without one (`expired_without_outcome`). Nothing
depends on a person's label: only states the object's own systems report, or the clock. It is attributed to
what the agent did, by method:

- `line`, deterministic: the line carries the exposure token of the card the agent showed, the token checks
  (the exposure token spec, section 4) and names an exposure of the subject at a position it had. An exposure
  inferred from a tool's result counts more than the person saw, and never attributes a line (interaction
  spec, 5.5).
- `order`, deterministic: a token on the object itself, or else the latest action an agent recorded on the
  object before the outcome (a proposal it filed, a payment it asked for), for every line no token of its
  own attributes.
- `identity`, probable: the subject engaged with the same item within the click window before the outcome,
  or was shown it within the view window. Kept for every line it finds, even one a token attributes, so the
  lines that have both calibrate it.
- `assisted_handoff`, probable: the object names the handoff that assisted it, and the outcome came within the
  handoff window after it. A handoff's id is a UUIDv7 minted when it was created, so it carries when.

A link carries its value whole, or none: a probable one keeps its confidence beside the value, never
`confidence x value`, and no report adds a probable value to a deterministic one (6.8). What a line and the
order are worth is the signed revenue definition's (`revenue`).
"""

import base64
import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from niadra.domain.common.ids import uuid7_ms
from niadra.domain.measure.revenue import (
    BY_LINE,
    SAME_ITEM,
    WHOLE_ORDER,
    OrderAmounts,
    Priced,
    Revenue,
    line_values,
    order_coupon,
    order_value,
)
from niadra.domain.measure.revenue import DEFAULT as DEFAULT_REVENUE
from niadra.domain.state.lifecycle import EXPIRED_WITHOUT_OUTCOME

PROVISIONAL = "provisional"
FINAL = "final"
EXPIRED = EXPIRED_WITHOUT_OUTCOME
DETERMINISTIC = "deterministic"
PROBABLE = "probable"
TOKEN_VERSION = "nx1"  # noqa: S105 (a format version, not a secret)
MAX_POSITION = 9999
MINOR_DIGITS = {"JPY": 0, "KRW": 0, "CLP": 0, "PYG": 0, "BHD": 3, "KWD": 3}
"""Digits after the point of the currencies that do not use two; every other currency uses two."""
UNUSABLE_FOR_LINES = frozenset({"tool_result"})

_POSITION = re.compile(r"^[1-9][0-9]{0,3}$")
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")


class TokenError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _verifier(prefix: str) -> str:
    return _b64(hashlib.sha256(prefix.encode()).digest())[:4]


def parse_token(token: str) -> tuple[UUID, int]:
    """The exposure and the position a token names, checked in the spec's order (section 4)."""
    parts = token.split(".")
    if len(parts) != 4:
        raise TokenError("malformed")
    version, encoded, position, verifier = parts
    if version != TOKEN_VERSION:
        raise TokenError("unsupported_version")
    if len(encoded) != 22 or not _B64.match(encoded):
        raise TokenError("malformed")
    raw = base64.urlsafe_b64decode(encoded + "==")
    if len(raw) != 16 or _b64(raw) != encoded:
        raise TokenError("malformed")
    if len(verifier) != 4 or not _B64.match(verifier):
        raise TokenError("malformed")
    if not _POSITION.match(position):
        raise TokenError("bad_position")
    if verifier != _verifier(f"{version}.{encoded}.{position}"):
        raise TokenError("bad_verifier")
    return UUID(bytes=raw), int(position)


def carries_token(state: Mapping[str, Any]) -> bool:
    """Whether an object's state holds an exposure token, on itself or on one of its lines: evidence that it
    is an outcome to attribute, whatever this process's configuration says yet."""
    values: list[Any] = list(state.values())
    for value in state.values():
        if isinstance(value, list):
            values += [v for entry in value if isinstance(entry, Mapping) for v in entry.values()]
    for value in values:
        if isinstance(value, str) and value.startswith(f"{TOKEN_VERSION}."):
            try:
                parse_token(value)
            except TokenError:
                continue
            return True
    return False


def minor_units(value: Any, currency: str) -> int | None:
    """A money value in the currency's minor units, rounded half to even; None for what is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    digits = MINOR_DIGITS.get(currency, 2)
    return int((amount * Decimal(10) ** digits).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True, slots=True)
class Shown:
    """An exposure of the subject, as the raw interactions (or the ones still pending) keep it."""

    exposure_id: str
    at: datetime
    positions: Mapping[int, str]
    """Position -> the object shown there."""
    turn_id: str | None = None
    agent: str | None = None
    method: str = "bridge"


@dataclass(frozen=True, slots=True)
class Touch:
    """The subject engaging with an object: a click, a detail, a mention, a cart."""

    ref: str
    at: datetime
    exposure_id: str | None = None
    turn_id: str | None = None
    agent: str | None = None


@dataclass(frozen=True, slots=True)
class Acted:
    """An action an agent recorded on the object."""

    action_id: UUID
    at: datetime
    agent: str | None = None


@dataclass(frozen=True, slots=True)
class Line:
    line_ref: str
    item: str | None = None
    value_minor: int | None = None
    """Its amount before discounts, coupons, shipping and taxes."""
    state: str | None = None
    stamp: str | None = None
    size: str | None = None
    return_reason: str | None = None
    discount_minor: int = 0
    tax_minor: int = 0
    replaces: str | None = None
    """The line of the same order it replaced, in an exchange."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """One object at one outcome: its state, the finality its type gives the state, and what it carries."""

    outcome: str
    object_id: UUID
    object_ref: str
    handle_id: UUID
    state: str
    finality: str
    reached_at: datetime
    success: bool
    lines: tuple[Line, ...] = ()
    value_minor: int | None = None
    """The object's own amount, for an outcome without lines, before discounts, coupon, shipping and taxes."""
    currency: str | None = None
    stamp: str | None = None
    handoff_id: UUID | None = None
    actions: tuple[Acted, ...] = ()
    discount_minor: int = 0
    coupon_minor: int = 0
    shipping_minor: int = 0
    tax_minor: int = 0
    purchase: str | None = None
    """The purchase the order was split from, by seller: `type:namespace:id`."""


@dataclass(frozen=True, slots=True)
class Rules:
    methods: frozenset[str]
    click_window: timedelta
    view_window: timedelta
    final_after: timedelta | None = None
    failed_line_states: frozenset[str] = frozenset()
    identity_confidence: float | None = None
    """How often the identity method names the exposure a line's own token names in this space, where both
    exist: the calibrated confidence each identity link carries. None until enough lines had both."""
    handoff_window: timedelta = timedelta(days=7)
    """How long after a handoff an outcome that names it counts as assisted by it."""
    revenue: Revenue = DEFAULT_REVENUE


@dataclass(frozen=True, slots=True)
class Link:
    link_id: UUID
    outcome: str
    object_id: UUID
    object_ref: str
    handle_id: UUID
    state: str
    line_ref: str | None
    method: str
    band: str
    finality: str
    valid_at: datetime
    value_minor: int | None
    currency: str | None
    exposure_id: str | None = None
    position: int | None = None
    turn_id: str | None = None
    action_id: UUID | None = None
    handoff_id: UUID | None = None
    agent: str | None = None
    confidence: float | None = None
    final_at: datetime | None = None
    order_ref: str | None = None
    """The order it counts in, when that is not the object itself: the purchase a split order's parts make."""


def link_id(object_id: UUID, line_ref: str | None, method: str, origin: str) -> UUID:
    """The same object, line, method and origin always make the same link: attributing again rewrites it."""
    digest = bytearray(
        hashlib.sha256(f"{object_id}|{line_ref or ''}|{method}|{origin}".encode()).digest()[:16]
    )
    digest[6] = (digest[6] & 0x0F) | 0x80  # RFC 9562 version 8: custom, hash-derived
    digest[8] = (digest[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(digest))


def finality_of(state: str, provisional: Iterable[str], final: Iterable[str]) -> str | None:
    """What the type's lifecycle makes of a state: provisional, final, or not an outcome yet."""
    if state in set(final):
        return FINAL
    if state in set(provisional):
        return PROVISIONAL
    return None


def attribute(outcome: Outcome, shown: Sequence[Shown], touches: Sequence[Touch], rules: Rules) -> list[Link]:
    """Every link of one outcome, by every method the space measures."""
    exposures = {s.exposure_id: s for s in shown}
    links: list[Link] = []

    def stamped(token: str | None) -> tuple[Shown, int] | None:
        if not token:
            return None
        try:
            exposure_id, position = parse_token(token)
        except TokenError:
            return None
        found = exposures.get(str(exposure_id))
        if found is None or found.method in UNUSABLE_FOR_LINES or position not in found.positions:
            return None
        return found, position

    order = stamped(outcome.stamp) if "order" in rules.methods else None
    acted = None
    if order is None and "order" in rules.methods:
        before = [a for a in outcome.actions if a.at <= outcome.reached_at]
        acted = max(before, key=lambda a: (a.at, str(a.action_id))) if before else None
    revenue = rules.revenue
    order_ref = outcome.purchase if revenue.split_orders == WHOLE_ORDER else None
    refs = {line.line_ref for line in outcome.lines}
    replacing = [line.replaces in refs - {line.line_ref} for line in outcome.lines]
    replaced = {line.replaces for line, swap in zip(outcome.lines, replacing, strict=True) if swap}
    amounts = OrderAmounts(
        outcome.value_minor, outcome.discount_minor, outcome.coupon_minor, outcome.shipping_minor,
        outcome.tax_minor,
    )  # fmt: skip
    if outcome.lines:
        priced = [
            Priced(line.value_minor, line.discount_minor, line.tax_minor, swap)
            for line, swap in zip(outcome.lines, replacing, strict=True)
        ]
        worth = line_values(priced, amounts, revenue)
    else:
        worth = [order_value(amounts, revenue)]
    lines: list[tuple[Line, int | None]] = []
    for line, value, swap in zip(outcome.lines or (Line(""),), worth, replacing or [False], strict=True):
        if swap and revenue.exchanges == SAME_ITEM:
            continue  # the same sale as the line it replaced, which keeps its value and attribution
        lines.append((line, value))
    kept = replaced if revenue.exchanges == SAME_ITEM else set()
    for line, worth_of in lines:
        value, state, finality = _line_value(outcome, line, worth_of, rules, line.line_ref in kept)
        base = Link(
            link_id=outcome.object_id, outcome=outcome.outcome, object_id=outcome.object_id,
            object_ref=outcome.object_ref, handle_id=outcome.handle_id, state=state,
            line_ref=line.line_ref or None, method="", band="", finality=finality,
            valid_at=outcome.reached_at,
            value_minor=value, currency=outcome.currency if value is not None else None,
            final_at=_final_at(outcome, rules, finality), order_ref=order_ref,
        )  # fmt: skip
        own = stamped(line.stamp) if "line" in rules.methods else None
        for method, found in (("line", own), ("order", None if own else order)):
            if found is None:
                continue
            exposure, position = found
            links.append(
                replace(
                    base,
                    link_id=link_id(outcome.object_id, line.line_ref, method, exposure.exposure_id),
                    method=method, band=DETERMINISTIC, exposure_id=exposure.exposure_id, position=position,
                    turn_id=exposure.turn_id, agent=exposure.agent,
                )
            )  # fmt: skip
        if own is None and order is None and acted is not None:
            links.append(
                replace(
                    base,
                    link_id=link_id(outcome.object_id, line.line_ref, "order", str(acted.action_id)),
                    method="order", band=DETERMINISTIC, action_id=acted.action_id, agent=acted.agent,
                )
            )  # fmt: skip
        if "identity" in rules.methods and line.item:
            near = _nearest(line.item, outcome.reached_at, shown, touches, rules)
            if near is not None:
                exposure_id, turn_id, agent = near
                origin = exposure_id or turn_id or ""
                links.append(
                    replace(
                        base,
                        link_id=link_id(outcome.object_id, line.line_ref, "identity", origin),
                        method="identity", band=PROBABLE, exposure_id=exposure_id, turn_id=turn_id,
                        agent=agent, confidence=rules.identity_confidence,
                    )
                )  # fmt: skip
    if "assisted_handoff" in rules.methods and _assisted(outcome, rules):
        assert outcome.handoff_id is not None
        known = [
            v
            for v in (_line_value(outcome, line, w, rules, line.line_ref in kept)[0] for line, w in lines)
            if v is not None
        ]
        value = max(0, sum(known) - order_coupon(amounts, revenue)) if known else None
        links.append(
            Link(
                link_id(outcome.object_id, None, "assisted_handoff", str(outcome.handoff_id)),
                outcome=outcome.outcome, object_id=outcome.object_id, object_ref=outcome.object_ref,
                handle_id=outcome.handle_id, state=outcome.state, line_ref=None, method="assisted_handoff",
                band=PROBABLE, finality=outcome.finality, valid_at=outcome.reached_at, value_minor=value,
                currency=outcome.currency if value is not None else None, handoff_id=outcome.handoff_id,
                final_at=_final_at(outcome, rules, outcome.finality), order_ref=order_ref,
            )
        )  # fmt: skip
    return links


def _assisted(outcome: Outcome, rules: Rules) -> bool:
    """The outcome came within the handoff window after the handoff it names. An id that is not a UUIDv7
    names no handoff Niadra created, and carries no time to check."""
    handoff = outcome.handoff_id
    if handoff is None or handoff.version != 7:
        return False
    at = datetime.fromtimestamp(uuid7_ms(handoff) / 1000, UTC)
    return at <= outcome.reached_at <= at + rules.handoff_window


def _line_value(
    outcome: Outcome, line: Line, value: int | None, rules: Rules, exchanged: bool
) -> tuple[int | None, str, str]:
    """A line's value, state and finality: a failed line (returned, cancelled) is final and worth nothing when
    the definition cancels by line, unless an exchange for the same item replaced it; and nothing is worth
    anything before a success state or after the deadline."""
    by_line = rules.revenue.cancellations == BY_LINE
    if by_line and not exchanged and line.state is not None and line.state in rules.failed_line_states:
        return None, line.state, FINAL
    if not outcome.success or outcome.finality == EXPIRED:
        return None, outcome.state, outcome.finality
    return value, outcome.state, outcome.finality


def _final_at(outcome: Outcome, rules: Rules, finality: str) -> datetime | None:
    if finality != PROVISIONAL or rules.final_after is None:
        return None
    return outcome.reached_at + rules.final_after


def _nearest(
    ref: str, at: datetime, shown: Sequence[Shown], touches: Sequence[Touch], rules: Rules
) -> tuple[str | None, str | None, str | None] | None:
    """The latest engagement with the item within the click window, else the latest exposure of it within
    the view window: `(exposure_id, turn_id, agent)`."""
    clicked = [t for t in touches if t.ref == ref and at - rules.click_window <= t.at <= at]
    if clicked:
        latest = max(clicked, key=lambda t: (t.at, t.exposure_id or ""))
        return latest.exposure_id, latest.turn_id, latest.agent
    seen = [s for s in shown if ref in s.positions.values() and at - rules.view_window <= s.at <= at]
    if seen:
        latest_shown = max(seen, key=lambda s: (s.at, s.exposure_id))
        return latest_shown.exposure_id, latest_shown.turn_id, latest_shown.agent
    return None


# Reports.


@dataclass(frozen=True, slots=True)
class BandTotal:
    """Outcomes and value of one method, band and finality: a probable value is never added to a
    deterministic one, and no value is ever multiplied by a confidence."""

    outcome: str
    method: str
    band: str
    finality: str
    currency: str
    outcomes: int
    lines: int
    value_minor: int


def totals(rows: Iterable[Mapping[str, Any]]) -> list[BandTotal]:
    """The daily rows added up per outcome, method, band, finality and currency, and nothing across them."""
    found: dict[tuple[str, str, str, str, str], list[int]] = {}
    for row in rows:
        key = (row["outcome"], row["method"], row["band"], row["finality"], row["currency"])
        acc = found.setdefault(key, [0, 0, 0])
        acc[0] += int(row["outcomes"])
        acc[1] += int(row["lines"])
        acc[2] += int(row["value_minor"])
    return [BandTotal(*key, *acc) for key, acc in sorted(found.items())]


@dataclass(frozen=True, slots=True)
class BiLine:
    """One line of the company's BI file: the order, the line (or none for a whole order), and its value."""

    order: str
    line: str
    value_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class Reconciled:
    deviation: float | None
    """|ours - BI| / BI over the lines both know; None when they share none."""
    within: bool
    matched: int
    only_ours: int
    only_bi: int
    ours_minor: int
    bi_minor: int
    currency: str | None
    worst: tuple[tuple[str, str, int, int], ...] = ()
    """The lines that differ most: order, line, ours and the BI's."""


def reconcile(
    ours: Mapping[tuple[str, str], int], bi: Sequence[BiLine], tolerance: float, *, whole_orders: bool = False
) -> Reconciled:
    """Our value of each line against the BI's, on the lines both know; the ones only one side knows are
    counted apart, never folded into the deviation. With `whole_orders` (a split purchase is one order), both
    sides are compared order by order, each order's lines added up."""
    if whole_orders:
        folded: dict[tuple[str, str], int] = {}
        for (order, _), value in ours.items():
            folded[(order, "")] = folded.get((order, ""), 0) + value
        ours, bi = folded, [replace(b, line="") for b in bi]
    theirs: dict[tuple[str, str], int] = {}
    currencies = {b.currency for b in bi}
    for b in bi:
        theirs[(b.order, b.line)] = theirs.get((b.order, b.line), 0) + b.value_minor
    both = sorted(set(ours) & set(theirs))
    ours_minor = sum(ours[k] for k in both)
    bi_minor = sum(theirs[k] for k in both)
    deviation = abs(ours_minor - bi_minor) / abs(bi_minor) if bi_minor else None
    worst = sorted(
        ((order, line, ours[(order, line)], theirs[(order, line)]) for order, line in both),
        key=lambda w: (-abs(w[2] - w[3]), w[0], w[1]),
    )
    return Reconciled(
        deviation=round(deviation, 6) if deviation is not None else None,
        within=deviation is not None and deviation <= tolerance,
        matched=len(both),
        only_ours=len(set(ours) - set(theirs)),
        only_bi=len(set(theirs) - set(ours)),
        ours_minor=ours_minor,
        bi_minor=bi_minor,
        currency=next(iter(currencies)) if len(currencies) == 1 else None,
        worst=tuple(w for w in worst[:10] if w[2] != w[3]),
    )
