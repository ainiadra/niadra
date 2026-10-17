"""Derivation by introspection (object type spec, section 8), run against the reference implementation below,
which follows the section's rules one by one: the fingerprint, the proposal and its review, and the changes
of a drift. Every proposal must also validate against the object type schema."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "vectors" / "type-derive.v0.json").read_text())["cases"]
SCHEMA = Draft202012Validator(json.loads((ROOT / "schemas" / "object-type.v0.json").read_text()))

RESERVED = {
    *("and", "or", "not", "in", "true", "false", "none", "yes", "no", "unobserved", "known_defect"),
    *("state", "derived_status", "watch_count", "purpose", "config"),
}
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
NUMBER = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")
NUMERIC = {"smallint", "integer", "bigint", "numeric", "real", "double precision"}
COLUMN = re.compile(r'^(?:[a-z_][a-z0-9_$]*|"(?:[^"]|"")+")$')
TYPES = {
    **dict.fromkeys(("text", "character varying", "character", "citext", "uuid"), "string"),
    **dict.fromkeys(NUMERIC, "number"),
    "money": "money",
    "boolean": "bool",
    "date": "date",
    "interval": "duration",
}


class RefusedError(Exception):
    pass


def canonical(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value).removesuffix(".0")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
    return "{" + ",".join(f"{json.dumps(k, ensure_ascii=False)}:{canonical(value[k])}" for k in keys) + "}"


def u16(text: str) -> bytes:
    return text.encode("utf-16-be")


# 8.6: parentheses, brackets and `::` count outside literals and quoted names.
def depths(text: str) -> list[int]:
    out, depth, quote, i = [], 0, "", 0
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(-1)
            if ch == quote and text[i + 1 : i + 2] == quote:
                out.append(-1)
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
            out.append(-1)
        elif ch in "([":
            out.append(depth)
            depth += 1
        elif ch in ")]":
            depth -= 1
            out.append(depth)
        else:
            out.append(depth)
        i += 1
    return out


def unwrap(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")") and 0 not in depths(text)[1:-1]:
        text = text[1:-1].strip()
    return text


def split(text: str, sep: str) -> list[str]:
    d, parts, start, i = depths(text), [], 0, 0
    while i <= len(text) - len(sep):
        if d[i] == 0 and text.startswith(sep, i):
            parts.append(text[start:i].strip())
            start = i = i + len(sep)
        else:
            i += 1
    return [*parts, text[start:].strip()]


def uncast(text: str) -> tuple[str, str]:
    text, cast = unwrap(text), ""
    while len(parts := split(text, "::")) > 1:
        cast, text = parts[-1], unwrap("::".join(parts[:-1]))
    return text, cast


def unquote(name: str) -> str:
    return name[1:-1].replace('""', '"') if name[:1] == '"' and name[-1:] == '"' else name


def constant(text: str) -> Any:
    bare, cast = uncast(text)
    if len(bare) >= 2 and bare[0] == bare[-1] == "'":
        inner = bare[1:-1]
        if "'" in inner.replace("''", ""):
            raise RefusedError
        inner = inner.replace("''", "'")
        if cast.split("(")[0].strip() in NUMERIC and NUMBER.match(inner):
            return float(inner) if "." in inner else int(inner)
        return inner
    if NUMBER.match(bare):
        return float(bare) if "." in bare else int(bare)
    raise RefusedError


def list_check(definition: str) -> tuple[str, list[Any]] | None:
    if not definition.startswith("CHECK "):
        return None
    text = unwrap(definition[6:].removesuffix(" NOT VALID"))
    sides = split(text, " = ")
    if len(sides) != 2:
        return None
    column = uncast(sides[0])[0]
    if not COLUMN.match(column):
        return None
    try:
        if sides[1].startswith("ANY (") and sides[1].endswith(")"):
            array = uncast(sides[1][5:-1])[0]
            if not (array.startswith("ARRAY[") and array.endswith("]")) or not array[6:-1].strip():
                return None
            values = [constant(item) for item in split(array[6:-1], ",")]
        else:
            values = [constant(sides[1])]
    except RefusedError:
        return None
    return unquote(column), values


# 8.5
def to_name(raw: str, limit: int = 64) -> str:
    out = "".join(ch.lower() if "A" <= ch <= "Z" else ch if re.match(r"[a-z0-9_]", ch) else "_" for ch in raw)
    out = (out if re.match(r"[a-z]", out) else "c_" + out)[:limit]
    return out + "_" if out in RESERVED else out


def unused(name: str, taken: set[str], limit: int = 64) -> str:
    n, candidate = 1, name
    while candidate in taken:
        n += 1
        candidate = name[: limit - len(f"_{n}")] + f"_{n}"
    taken.add(candidate)
    return candidate


# 8.2
def normalize(catalog: dict[str, Any]) -> dict[str, Any]:
    if catalog["catalog"] != "postgresql":
        raise RefusedError("unknown_catalog")
    if len(split(catalog["table"], ".")) != 2:
        raise RefusedError("invalid_catalog")
    if not catalog["columns"]:
        raise RefusedError("no_columns")
    return {
        "catalog": "postgresql",
        "table": catalog["table"],
        "columns": sorted(catalog["columns"], key=lambda c: u16(c["name"])),
        "primary_key": catalog["primary_key"],
        "checks": sorted(catalog["checks"], key=u16),
        "enums": sorted(catalog["enums"], key=lambda e: u16(e["name"])),
        "foreign_keys": sorted(catalog["foreign_keys"], key=lambda f: u16(canonical(f))),
        "triggers": sorted(catalog["triggers"], key=lambda t: u16(t["name"])),
    }


def field_type(column: dict[str, Any], fks: set[str], lists: dict[str, Any]) -> str | None:
    if column["name"] in fks:
        return "ref"
    if "enum" in column or column["name"] in lists:
        return "enum"
    if column["type"].endswith("[]"):
        return "list"
    base = column["type"].split("(")[0].strip()
    return "datetime" if base.startswith("timestamp") else TYPES.get(base)


# 8.4 and 8.7
def derive(catalog: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    cat = normalize(catalog)
    ownership = options.get("ownership", "subject")
    if ownership not in ("subject", "shared"):
        raise RefusedError("ownership_not_derivable")
    name = options.get("type", to_name(unquote(split(cat["table"], ".")[1]), 40))
    system = options.get("system", "postgresql")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", name):
        raise RefusedError("invalid_name")
    if not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,63}", system):
        raise RefusedError("invalid_name")
    fingerprint = "sha256:" + hashlib.sha256(canonical(cat).encode()).hexdigest()
    taken: set[str] = set()
    names = {c["name"]: unused(to_name(c["name"]), taken) for c in cat["columns"]}
    lists: dict[str, Any] = {}
    unread = []
    for definition in cat["checks"]:
        found = list_check(definition)
        if found and found[0] in names:
            lists.setdefault(*found)
        else:
            unread.append(definition)
    fks = {f["columns"][0] for f in cat["foreign_keys"] if len(f["columns"]) == 1}
    review: list[dict[str, Any]] = []
    fields: dict[str, Any] = {}
    for column in cat["columns"]:
        kind = field_type(column, fks, lists)
        if kind is None or len(fields) >= 200:
            review.append({"kind": "column", "column": column["name"], "type": column["type"]})
        else:
            fields[names[column["name"]]] = {"type": kind}
    review += [{"kind": "check", "definition": d} for d in unread]
    out: dict[str, Any] = {
        "type": name,
        "version": "1",
        "ownership": ownership,
        "mirror_of": {
            "system": system,
            "derived_by": "introspection",
            "fingerprint": fingerprint,
            "drift": "alert",
        },
        "fields": fields,
    }
    key = cat["primary_key"]
    if key and len(key) <= 8 and all(names[c] in fields for c in key):
        out["key"] = {"natural": [names[c] for c in key]}
    status = next((c for c in ("status", "state") if c in names and names[c] in fields), None)
    if status:
        column = next(c for c in cat["columns"] if c["name"] == status)
        labels = {e["name"]: e["labels"] for e in cat["enums"]}
        values = labels[column["enum"]] if "enum" in column else lists.get(status)
        if values is not None:
            if 1 <= len(values) <= 50 and all(isinstance(v, str) and NAME.match(v) for v in values):
                out["states"] = list(dict.fromkeys(values))
            else:
                review.append({"kind": "states", "column": status})
    relations: dict[str, Any] = {}
    roles: set[str] = set()
    for fk in cat["foreign_keys"]:
        field = names[fk["columns"][0]] if len(fk["columns"]) == 1 else None
        if field is None or field not in fields or len(relations) >= 20:
            review.append({"kind": "foreign_key", "columns": fk["columns"], "references": fk["references"]})
            continue
        role = field.removesuffix("_id") if field.endswith("_id") else field
        role = role if NAME.match(role) and role not in RESERVED else field
        relations[unused(role, roles)] = {
            "type": to_name(unquote(split(fk["references"], ".")[1]), 40),
            "via": field,
        }
    if relations:
        out["relations"] = relations
    review += [{"kind": "trigger", "name": t["name"], "enabled": t["enabled"]} for t in cat["triggers"]]
    return {"fingerprint": fingerprint, "type": out, "review": review}


# 8.8.2
def changes(declared: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    before, after = declared.get("fields", {}), live.get("fields", {})
    removed = {f for f in before if f not in after}
    retyped = {f for f in before if f in after and before[f]["type"] != after[f]["type"]}
    s0, s1 = set(declared.get("states", [])), set(live.get("states", []))
    r0, r1 = set(declared.get("relations", {})), set(live.get("relations", {}))
    touched = removed | retyped
    if s0 != s1:
        touched |= set([f for f in ("status", "state_") if f in before][:1])
    return {
        "fields_added": len(set(after) - set(before)),
        "fields_removed": len(removed),
        "fields_retyped": len(retyped),
        "states_added": len(s1 - s0),
        "states_removed": len(s0 - s1),
        "relations_added": len(r1 - r0),
        "relations_removed": len(r0 - r1),
        "key_changed": declared.get("key", {}).get("natural", []) != live.get("key", {}).get("natural", []),
        "fields": sorted(touched, key=u16)[:50],
    }


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_type_derive(case: dict[str, Any]) -> None:
    if case["op"] == "changes":
        assert changes(case["declared"], case["live"]) == case["expect"]["changes"]
        return
    if "error" in case["expect"]:
        with pytest.raises(RefusedError, match=f"^{case['expect']['error']}$"):
            derive(case["catalog"], case["options"])
        return
    got = derive(case["catalog"], case["options"])
    assert got == case["expect"]
    assert not list(SCHEMA.iter_errors(got["type"])), case["id"]


def test_the_same_catalog_in_another_order_has_the_same_fingerprint() -> None:
    by_id = {case["id"]: case for case in CASES}
    first, second = by_id["derive.retail.orders"], by_id["derive.retail.orders_listed_in_another_order"]
    assert first["catalog"] != second["catalog"]
    assert first["expect"] == second["expect"]


def test_a_new_value_in_a_check_is_drift() -> None:
    by_id = {case["id"]: case for case in CASES}
    before, after = by_id["derive.retail.orders"], by_id["derive.retail.orders_new_status_value"]
    assert before["expect"]["fingerprint"] != after["expect"]["fingerprint"]
    assert changes(before["expect"]["type"], after["expect"]["type"])["states_added"] == 1
