"""Guard lines (13, 6.1): memory that states a limit, not only a fact.

When the customer's turn asks for a kind of value the space has seen agents contradict (a date, an
amount, a protocol, an order number), the slots carry one derived line per such type, written by rule
from the settled value that holds for it: the value, who stated it and when, and the negative
instruction of the type: "[Guarda] data 18/09/2026, sistema 16/09: não afirme outra; outra do cliente
é pendência"."

Which value holds is decided by precedence, never by a model: the system of record, then a human
agent; the most recent statement within the same origin. A value only the customer or an AI agent
stated writes no guard: the agent must not be held to what another model or the customer said. A
type the space never measured a contradiction for writes no guard either, so the guard costs tokens
only where the measurement says it pays; and a stable fraction of customers, the control group, never
gets one, so the effect is measured against them.

`violates` is the rule measurement applies to the agent's answer after a guard (and the SDKs apply at
once): the answer states a value of the guarded type that is not the guarded one, and never the
guarded one. Nothing here reads a clock or calls a model.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from niadra.domain.compile.model import Section
from niadra.domain.compile.templates import Formatter
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.memory.values import MARKER, SettledValue, ValueType, find_values, origin_rank, value_key
from niadra.domain.serve.holdout import draw
from niadra.domain.serve.turn import QuestionKind, TurnAnalysis
from niadra.domain.vocabulary import Speaker

GUARD_KIND = "guard"
MAX_TOKENS = 30
"""A guard line never costs more than this, by the pack's own estimate (which errs upward)."""
MAX_GUARDS = 2
"""Guards per turn at most: a turn asks for one or two kinds of value."""
WINDOW_DAYS = 30
"""A type writes guards while the space measured a contradiction of it in this many days."""
CONTROL_FRACTION = 0.2
"""Customers drawn into the control group: the guard is computed for them, recorded, never shown."""
GUARDS_ARM = "guards"
CONTROL_ARM = "control"
VALUE_ARM = "value"
"""The arm of a settled value slotted without a guard: what the space's contradictions are counted on."""
AUTHORITIES = frozenset({Speaker.SYSTEM, Speaker.HUMAN_AGENT})
"""Who may state a value a guard holds the agent to."""

QUESTION_TYPES = {QuestionKind.WHEN: ValueType.DATE}
"""A question asks for a kind of value without naming it: "when is the visit?" asks for a date."""

_TYPES = frozenset(t.value for t in ValueType)


@dataclass(frozen=True, slots=True)
class Guard:
    value_id: UUID
    type: ValueType
    raw: str
    origin: Speaker
    said_at: datetime
    line: str
    tokens: int

    @property
    def item_id(self) -> str:
        return f"value:{self.value_id}"

    @property
    def rule(self) -> str:
        return f"guard_{self.type.value}"

    @property
    def short_id(self) -> str:
        return short_id(str(self.value_id))


def short_id(public_id: str) -> str:
    """The id a pack line carries and an agent's turn names (context-pack.v1): the last eight hex digits
    of the item's public id, the random part of a UUIDv7."""
    return public_id.replace("-", "")[-8:]


def arm(space_id: UUID, profile_id: UUID) -> str:
    """The customer's arm of the guard experiment: stable per space and profile, on every channel."""
    return CONTROL_ARM if draw(f"guards:{space_id}", profile_id) < CONTROL_FRACTION else GUARDS_ARM


def turn_types(turn: TurnAnalysis) -> tuple[ValueType, ...]:
    """The kinds of value the turn asks for, named ("o protocolo") or implied by the question ("quando")."""
    found = [ValueType(t) for t in turn.asked_types if t in _TYPES]
    implied = QUESTION_TYPES.get(turn.question)
    if implied is not None:
        found.append(implied)
    return tuple(dict.fromkeys(found))


def wanted(turn: TurnAnalysis, measured: frozenset[str]) -> tuple[ValueType, ...]:
    """The types this turn may write a guard for: asked, and measured as contradicted in the space."""
    return tuple(t for t in turn_types(turn) if t.value in measured)[:MAX_GUARDS]


def authority(values: Iterable[SettledValue], type: ValueType) -> SettledValue | None:
    """The value of `type` a guard may hold the agent to: precedence by origin (system of record, human
    agent, AI agent, customer), then the latest statement; None when the winner is not an authority.
    A value holding a hold marker is never one."""
    typed = [v for v in values if v.type is type and not MARKER.search(v.raw)]
    if not typed:
        return None
    best = max(typed, key=lambda v: (origin_rank(v.speaker), v.said_at, v.key))
    return best if best.speaker in AUTHORITIES else None


def guards_for(
    turn: TurnAnalysis,
    values: Sequence[SettledValue],
    measured: frozenset[str],
    *,
    fmt: Formatter,
    voice: bool,
) -> tuple[Guard, ...]:
    """The guard lines of one turn, in the order the types were asked. `values` are the customer's
    settled values the reader may see (the caller filters them by the same policy as the slots)."""
    found = []
    for type in wanted(turn, measured):
        value = authority(values, type)
        if value is None:
            continue
        line = render(value, fmt, voice=voice)
        if line is None:
            continue
        found.append(
            Guard(value.value_id, type, value.raw, value.speaker, value.said_at, line, _tokens(line))
        )
    return tuple(found)


def render(value: SettledValue, fmt: Formatter, *, voice: bool) -> str | None:
    """The line, within `MAX_TOKENS`: with who stated the value and when; else without; else only the
    negative instruction; None when even that is longer (a value that long is no number to repeat)."""
    common = {
        "term": fmt.term(value.type.value),
        "value": value.raw,
        "other": fmt.word(f"guard_other_{'date' if value.type is ValueType.DATE else 'value'}"),
    }
    origin = {"origin": fmt.word(f"short_{value.speaker.value}"), "day": fmt.day(value.said_at)}
    for key in ("guard_line", "guard_line_short", "guard_line_minimal"):
        text = fmt.word(key, **common, **origin)
        line = fmt.line(Section.META, text, voice=voice).replace(
            fmt.section(Section.META), fmt.word("guard"), 1
        )
        if _tokens(line) <= MAX_TOKENS:
            return line
    return None


def _tokens(line: str) -> int:
    return estimate_tokens(line + "\n")


# What the agent said against a guard.


def violates(type: ValueType | str, guarded: str, text: str) -> bool:
    """The agent's answer states a value of the guarded type other than the guarded one, and never the
    guarded one: "a visita é dia 19/09" against a guard of 18/09. Saying both ("não é 19/09, é 18/09")
    is no violation, and neither is a value of another type."""
    kind = ValueType(type)
    said = [f for f in find_values(text) if f.type is kind]
    return bool(said) and not (any(same(kind, f.raw, guarded) for f in said) or _names(text, guarded))


def same(type: ValueType, a: str, b: str) -> bool:
    """Two writings of one value: the same date (a missing year matches any), the same amount, or the
    same key (`45778-204` and `45778204`)."""
    if type is ValueType.DATE:
        left, right = date_parts(a), date_parts(b)
        if left is not None and right is not None:
            return left[:2] == right[:2] and (left[2] is None or right[2] is None or left[2] == right[2])
    if type is ValueType.AMOUNT:
        left_amount, right_amount = amount(a), amount(b)
        if left_amount is not None and right_amount is not None:
            return left_amount == right_amount
    return value_key(a) == value_key(b)


def _names(text: str, guarded: str) -> bool:
    key = value_key(guarded)
    return bool(key) and any(value_key(w) == key for w in text.split())


_DMY = re.compile(r"^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?$")
_YMD = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def date_parts(raw: str) -> tuple[int, int, int | None] | None:
    """(day, month, year or None) of a written date: `18/09`, `18/09/2026`, `2026-09-18`."""
    text = raw.strip().rstrip(".,;:!?)")
    if m := _YMD.match(text):
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    if m := _DMY.match(text):
        year = m.group(3)
        full = None if year is None else int(year) + (2000 if len(year) == 2 else 0)
        return int(m.group(1)), int(m.group(2)), full
    return None


_MONEY = re.compile(r"[^\d.,]")


def amount(raw: str) -> Decimal | None:
    """The amount a token writes, in either convention: `1.234,56`, `1,234.56`, `249,90`, `R$249.90`."""
    text = _MONEY.sub("", raw).strip(".,")
    if not text:
        return None
    last = max(text.rfind(","), text.rfind("."))
    if last >= 0 and len(text) - last - 1 in (1, 2):
        whole, cents = text[:last], text[last + 1 :]
    else:
        whole, cents = text, ""
    whole = whole.replace(",", "").replace(".", "")
    try:
        return Decimal(f"{whole or '0'}.{cents or '0'}")
    except InvalidOperation:
        return None
