"""An object's identifier written as it is in a conversation: a case number, an order number,
a plate. Pure.

A value names its object in a text only when nothing else could write it by chance. `distinctive` is that
test, the same for every company and language:
- at least `MIN_DIGITS` digits: no word of any language has them, so a status ("aberto"), a name or a city
  never qualifies. A natural key (`key.natural`) needs `NATURAL_MIN_DIGITS`: the company declared that field
  identifies its object, so "PED-77" there is an order number, where an ordinary field's "PED-77" could be
  any code. No token of 5 or more characters with one or two digits was written in any of 2,104 benchmark and
  legal turns (08/10/2026): the 236 written were "17th", "20h", "30", all shorter;
- from `MIN_CHARS` to `MAX_CHARS` characters once normalized: shorter is a quantity or a time ("10h",
  "2026"), longer is a description, not an identifier;
- not a date or an instant, nor an amount with cents ("45000.00"): a date written in a turn says when and
  an amount says how much, not which object.
Matching is verbatim after `normalized` (case and runs of spaces), at the boundaries of the value: "12345"
is not written inside "123456".
"""

import re
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime

from niadra.domain.objects.undeclared import held

MIN_DIGITS = 3
NATURAL_MIN_DIGITS = 1
MIN_CHARS = 5
MAX_CHARS = 64
_SPACES = re.compile(r"\s+")
_AMOUNT = re.compile(r"[+-]?\d[\d.,]*[.,]\d{1,2}")


def normalized(text: str) -> str:
    return _SPACES.sub(" ", text).strip().casefold()


def distinctive(value: str, *, natural: bool = False) -> bool:
    """Whether `value`, written in a text, could only be that identifier (module docstring); `natural` when
    its field is one of its type's natural keys."""
    said = normalized(value)
    digits = NATURAL_MIN_DIGITS if natural else MIN_DIGITS
    if not MIN_CHARS <= len(said) <= MAX_CHARS or sum(c.isdigit() for c in said) < digits:
        return False
    if _AMOUNT.fullmatch(said):
        return False
    try:
        datetime.fromisoformat(said) if "t" in said else date.fromisoformat(said)
    except ValueError:
        return True
    return False


def writes(text: str, value: str) -> bool:
    """Whether the `normalized` text writes the `normalized` value whole, not as part of a longer token."""
    start = text.find(value)
    while start != -1:
        end = start + len(value)
        before = text[start - 1] if start else " "
        after = text[end] if end < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = text.find(value, start + 1)
    return False


_OPAQUE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def opaque(value: object) -> bool:
    """Whether a value is an opaque id (a UUID): a model can say nothing of one."""
    return isinstance(value, str) and _OPAQUE.fullmatch(value.strip()) is not None


def goes_by(
    state: Mapping[str, object],
    names: Sequence[str],
    shaped: Collection[str] = (),
    natural: Collection[str] = (),
) -> tuple[str, str] | None:
    """The field and value an object goes by on a line: the first of `names` (its type's written keys) it
    holds, a `shaped` one (`key.match`) with any value, any other only with a value specific enough to name it
    (`distinctive`, `natural` for its natural keys), never an opaque id. None when it holds none: it goes by
    its ref."""
    for name in names:
        value = state.get(name)
        if not isinstance(value, str | int) or str(value).strip() == "" or opaque(value):
            continue
        # A value that may be personal never names an object on a line, whatever field holds it.
        if held(name, str(value)) is not None:
            continue
        if name in shaped or distinctive(str(value), natural=name in natural):
            return name, str(value)
    return None
