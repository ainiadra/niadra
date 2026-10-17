"""The `where` expression of history navigation: AND, OR and NOT over a fixed set of row fields (16.2).

Checked in memory over the profile's own rows after the policy, so it can only narrow what the reader
may already see. The grammar follows the filter language agent developers know: a bare value means
equality, a list means `in`, sibling keys are ANDed and an operator object ANDs its operators.
"""

import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

MAX_DEPTH = 6
MAX_CONDITIONS = 64
MAX_LIST = 100
MAX_TEXT = 500

_TIME = frozenset({"at", "valid_until"})
_NUMBER = frozenset({"confidence"})
_TEXT = frozenset({"text"})
_STRING = frozenset(
    {"id", "kind", "channel", "category", "outcome", "source_id", "vendor", "object_type", "object_namespace"}
)
FIELDS = _TIME | _NUMBER | _TEXT | _STRING

_EQUALITY = frozenset({"eq", "ne", "in", "nin", "exists"})
_ORDER = frozenset({"gt", "gte", "lt", "lte"})
_OPERATORS: dict[str, frozenset[str]] = {
    **{f: _EQUALITY | _ORDER for f in _TIME | _NUMBER},
    **{f: frozenset({"contains", "icontains", "exists"}) for f in _TEXT},
    **{f: _EQUALITY | frozenset({"contains", "icontains"}) for f in _STRING},
}

Row = Mapping[str, Any]
Predicate = Callable[[Row], bool]


class WhereError(ValueError):
    """The expression does not parse; the message names the path, never a value (7.0)."""


@dataclass(frozen=True, slots=True)
class Where:
    """A parsed expression: `matches` reads a row as a mapping of the fields above."""

    predicate: Predicate

    def matches(self, row: Row) -> bool:
        return self.predicate(row)


def parse_where(tree: Any) -> Where:
    return Where(_node(tree, "where", 1, [0]))


def _node(node: Any, path: str, depth: int, counter: list[int]) -> Predicate:
    if depth > MAX_DEPTH:
        raise WhereError(f"{path}: nested deeper than {MAX_DEPTH} levels")
    if not isinstance(node, dict) or not node:
        raise WhereError(f"{path}: expected a non-empty object")
    parts = [_entry(key, value, f"{path}.{key}", depth, counter) for key, value in node.items()]
    return parts[0] if len(parts) == 1 else _all(parts)


def _entry(key: str, value: Any, path: str, depth: int, counter: list[int]) -> Predicate:
    if key in ("AND", "OR"):
        if not isinstance(value, list) or not value:
            raise WhereError(f"{path}: expected a non-empty list")
        children = [_node(v, f"{path}[{i}]", depth + 1, counter) for i, v in enumerate(value)]
        return _all(children) if key == "AND" else _any(children)
    if key == "NOT":
        # One object, or a list of them read as their AND, as the grammar agent developers know allows.
        if isinstance(value, list):
            if not value:
                raise WhereError(f"{path}: expected a non-empty list")
            inner = _all([_node(v, f"{path}[{i}]", depth + 1, counter) for i, v in enumerate(value)])
        else:
            inner = _node(value, path, depth + 1, counter)
        return lambda row: not inner(row)
    if key not in FIELDS:
        raise WhereError(f"{path}: not a field; use one of {', '.join(sorted(FIELDS))} or AND, OR, NOT")
    if isinstance(value, dict):
        if not value:
            raise WhereError(f"{path}: expected at least one operator")
        return _all([_condition(key, op, arg, f"{path}.{op}", counter) for op, arg in value.items()])
    return _condition(key, "in" if isinstance(value, list) else "eq", value, path, counter)


def _condition(field: str, op: str, arg: Any, path: str, counter: list[int]) -> Predicate:
    counter[0] += 1
    if counter[0] > MAX_CONDITIONS:
        raise WhereError(f"more than {MAX_CONDITIONS} conditions")
    if op not in _OPERATORS[field]:
        raise WhereError(f"{path}: `{op}` does not apply to {field}")
    if op == "exists":
        if not isinstance(arg, bool):
            raise WhereError(f"{path}: expected true or false")
        return lambda row: (row.get(field) is not None) is arg
    if op in ("in", "nin"):
        if not isinstance(arg, list) or not arg or len(arg) > MAX_LIST:
            raise WhereError(f"{path}: expected a list of 1 to {MAX_LIST} values")
        values = frozenset(_value(field, v, path) for v in arg)
        if op == "in":
            return lambda row: row.get(field) in values
        return lambda row: row.get(field) not in values
    if op in ("contains", "icontains"):
        if not isinstance(arg, str) or not arg or len(arg) > MAX_TEXT:
            raise WhereError(f"{path}: expected text of 1 to {MAX_TEXT} characters")
        if op == "contains":
            return lambda row: isinstance(text := row.get(field), str) and arg in text
        needle = fold(arg)
        return lambda row: isinstance(text := row.get(field), str) and needle in fold(text)
    wanted = _value(field, arg, path)
    if op == "eq":
        return lambda row: row.get(field) == wanted
    if op == "ne":
        # A row without the field is not equal to anything, as in the language this one follows.
        return lambda row: row.get(field) != wanted
    compare = _COMPARE[op]
    return lambda row: (got := row.get(field)) is not None and compare(got, wanted)


_COMPARE: dict[str, Callable[[Any, Any], bool]] = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
}


def _value(field: str, value: Any, path: str) -> Any:
    if field in _TIME:
        return _instant(value, path)
    if field in _NUMBER:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise WhereError(f"{path}: expected a number")
        return float(value)
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise WhereError(f"{path}: expected text of 1 to {MAX_TEXT} characters")
    return value


def _instant(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        raise WhereError(f"{path}: expected an ISO 8601 date or date-time")
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return datetime(day.year, day.month, day.day, tzinfo=UTC)
        at = datetime.fromisoformat(value)
    except ValueError:
        raise WhereError(f"{path}: expected an ISO 8601 date or date-time") from None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _all(parts: list[Predicate]) -> Predicate:
    return lambda row: all(p(row) for p in parts)


def _any(parts: list[Predicate]) -> Predicate:
    return lambda row: any(p(row) for p in parts)


def fold(text: str) -> str:
    """Case and accents folded: `icontains` finds `fatura` in `Fatura`, `cancelacion` in `cancelación`."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))
