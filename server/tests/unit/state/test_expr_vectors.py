"""The reference implementation of niadra-expr against the spec's vectors.

`vectors/niadra-expr.v0.json` is a copy of `spec/vectors/niadra-expr.v0.json` in the monorepo, the same file
both SDKs run; when the spec changes, copy it again. The reader fails on a field it does not know, so a vector
written for a later version never passes by being half read.
"""

import json
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from niadra.domain.state.expr import (
    Calendar,
    Environment,
    ExprError,
    Kind,
    Slot,
    Value,
    absent,
    boolean,
    epoch_ms,
    evaluate,
    format_datetime,
    parse,
    unknown,
)
from niadra.domain.state.logic import Logic

VECTORS = json.loads((Path(__file__).parent / "vectors" / "niadra-expr.v0.json").read_text())
CASE_KEYS = {"id", "expr", "input", "expect"}
INPUT_KEYS = {
    "now",
    "utc_offset",
    "fields",
    "inputs",
    "absent_names",
    "config",
    "quotes",
    "calendars",
    "state",
    "derived_status",
    "watch_count",
    "purpose",
    "presented_rank",
}
SLOT_KEYS = {"type", "v", "logic", "absent", "at", "observer", "was", "completeness"}
PREVIOUS_KEYS = {"type", "v", "logic", "absent"}
KIND_OF_TYPE = {
    "string": Kind.STRING,
    "text": Kind.STRING,
    "enum": Kind.STRING,
    "ref": Kind.STRING,
    "number": Kind.NUMBER,
    "money": Kind.NUMBER,
    "percent": Kind.NUMBER,
    "date": Kind.DATE,
    "datetime": Kind.DATETIME,
    "duration": Kind.DURATION,
    "bool": Kind.BOOL,
    "list": Kind.LIST,
}


def instant(text: str) -> int:
    moment = datetime.fromisoformat(text)
    assert moment.tzinfo is not None, "times carry their offset"
    return epoch_ms(moment)


def scalar(raw: Any) -> Value:
    if isinstance(raw, bool):
        return boolean(raw)
    if isinstance(raw, int | float):
        return Value(Logic.YES, Kind.NUMBER, float(raw))
    assert isinstance(raw, str)
    return Value(Logic.YES, Kind.STRING, raw)


def datum(kind: Kind, raw: Any) -> object:
    match kind:
        case Kind.NUMBER:
            return float(raw)
        case Kind.DATE:
            return date.fromisoformat(raw)
        case Kind.DATETIME:
            return instant(raw)
        case Kind.DURATION:
            assert isinstance(raw, int)
            return raw
        case Kind.LIST:
            return tuple(scalar(item) for item in raw)
    return raw


def value_of(raw: Mapping[str, Any], keys: set[str]) -> Value:
    assert set(raw) <= keys, f"unknown slot fields {set(raw) - keys}"
    v = raw.get("v")
    kind = KIND_OF_TYPE[raw["type"]] if "type" in raw else None
    if kind is None and v is not None:
        kind = scalar(v).kind
    default = (Logic.YES if v is not False else Logic.NO) if v is not None else None
    logic = Logic(raw["logic"]) if "logic" in raw else default or Logic.UNOBSERVED
    if kind is Kind.BOOL and logic.known:
        return boolean(logic is Logic.YES)
    if logic is Logic.NO:
        return absent(raw.get("absent"))
    if not logic.known:
        return unknown(logic)
    assert kind is not None
    return Value(Logic.YES, kind, datum(kind, v))


def slot_of(raw: Mapping[str, Any]) -> Slot:
    return Slot(
        value_of({k: v for k, v in raw.items() if k in PREVIOUS_KEYS}, SLOT_KEYS),
        at=instant(raw["at"]) if "at" in raw else None,
        observer=raw.get("observer"),
        was=value_of(raw["was"], PREVIOUS_KEYS) if "was" in raw else None,
        completeness=raw.get("completeness"),
    )


def offset_minutes(text: str) -> int:
    sign = -1 if text.startswith("-") else 1
    hours, minutes = text[1:].split(":")
    return sign * (int(hours) * 60 + int(minutes))


def environment(raw: Mapping[str, Any]) -> Environment:
    assert set(raw) <= INPUT_KEYS, f"unknown input fields {set(raw) - INPUT_KEYS}"
    for slot in [*raw.get("fields", {}).values(), *raw.get("inputs", {}).values()]:
        assert set(slot) <= SLOT_KEYS, f"unknown slot fields {set(slot) - SLOT_KEYS}"
    return Environment(
        now=instant(raw["now"]),
        utc_offset_min=offset_minutes(raw.get("utc_offset", "+00:00")),
        fields={name: slot_of(slot) for name, slot in raw.get("fields", {}).items()},
        inputs={name: slot_of(slot) for name, slot in raw.get("inputs", {}).items()},
        absent_names=frozenset(raw.get("absent_names", [])),
        config={key: absent() if v is None else scalar(v) for key, v in raw.get("config", {}).items()},
        quotes={
            source: {name: slot_of(slot) for name, slot in quote.items()}
            for source, quote in raw.get("quotes", {}).items()
        },
        calendars={
            name: Calendar(
                holidays=frozenset(date.fromisoformat(day) for day in calendar.get("holidays", [])),
                weekend=frozenset(calendar.get("weekend", [6, 7])),
            )
            for name, calendar in raw.get("calendars", {}).items()
        },
        state=raw.get("state"),
        derived_status=raw.get("derived_status"),
        watch_count=raw.get("watch_count", 0),
        purpose=raw.get("purpose", "display"),
        presented_rank=raw.get("presented_rank"),
    )


def encode(value: Value) -> dict[str, Any]:
    """A result as the vectors write it: the logical value, and the kind and datum of a known value."""
    if not value.logic.known:
        return {"logic": value.logic.value}
    if value.is_absent:
        return {"logic": "no", **({"absent": value.absent} if value.absent else {})}
    assert value.kind is not None
    out: dict[str, Any] = {"type": value.kind.value, "logic": value.logic.value}
    match value.kind:
        case Kind.DATE:
            assert isinstance(value.datum, date)
            out["v"] = value.datum.isoformat()
        case Kind.DATETIME:
            assert isinstance(value.datum, int)
            out["v"] = format_datetime(value.datum)
        case Kind.LIST:
            assert isinstance(value.datum, tuple)
            out["v"] = [encode(item) for item in value.datum]
        case _:
            out["v"] = value.datum
    return out


def same(expected: Any, actual: Any) -> bool:
    """Numbers compare by value (`42` and `42.0` are one number); everything else exactly."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(same(expected[k], actual[k]) for k in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(same(e, a) for e, a in zip(expected, actual, strict=True))
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        if isinstance(expected, bool) or isinstance(actual, bool):
            return expected is actual
        return float(expected) == float(actual)
    return bool(expected == actual)


def test_the_envelope_is_the_one_of_the_layout() -> None:
    assert set(VECTORS) == {"vectors", "version", "spec", "cases"}
    assert (VECTORS["vectors"], VECTORS["version"]) == ("niadra-expr", "v0")
    ids = [case["id"] for case in VECTORS["cases"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["id"])
def test_vector(case: dict[str, Any]) -> None:
    assert set(case) == CASE_KEYS, f"unknown case fields {set(case) - CASE_KEYS}"
    assert set(case["expect"]) in ({"value"}, {"error"})
    env = environment(case["input"])
    try:
        result: dict[str, Any] = {"value": encode(evaluate(parse(case["expr"]), env))}
    except ExprError as exc:
        result = {"error": exc.code}
    assert same(case["expect"], result), result
