"""The object types a space's traffic shows, for the types a space did not declare.
Pure.

A company connects its systems and never declares a type: what its system events carry becomes the space's
observed types, under whatever the company declared (P2: a declared type is never touched). The evidence is
what the objects' states held, counted change by change (`seen`); the observed types are computed from it
(`observed_types`) and only say what serving a field cannot:
- the type, on its first object, mirroring the system whose events made it;
- its states, from its `state` values in the order they first appeared, and as final outcomes the states
  objects stayed in for `FINAL_AFTER` on at least `FINAL_OBJECTS` objects;
- a relation, when a field's values named objects of another type at least `RELATION_MATCHES` times and
  never another type;
- a due date, when a date field's name says due and most of its values were ahead of the event that
  carried them, with a notice `NOTICE_BEFORE` before it;
- its identifiers, the text fields every value of which was specific enough to name an object
  (`written.distinctive`: a case or order number) in at least `IDENTIFIER_VALUES` values, none ever held: so
  the object goes by it on a line and a conversation that writes it finds the object.

Across types, the operations the systems' events confirm (`operation`, 5.8) are counted too, and the ones seen
`OPERATION_EVENTS` times become the space's observed operations (`observed_operations`): an open item said
before the event that does it can then wait for it, as a declared vocabulary would let it.

Every other field stays undeclared on purpose: an undeclared field is served by its name made readable and
held, value by value, whenever a value may be personal or is free text (`domain.objects.undeclared`). The
identifiers it declares keep that hold too (`ObjectType.observed_fields`): no person ruled them not personal,
so one personal value sent later never reaches an agent (P3). Names are data (P6): a type, field or state
name outside the identifier shape is left out and counted. Inference is bounded (P5): past `MAX_TYPES`,
`MAX_FIELDS` or `MAX_STATES`, new shapes are stored and served as they are, and counted, never inferred.
"""

import hashlib
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from niadra.domain.objects.operations import MAX_OPERATIONS, canonical_operation
from niadra.domain.objects.state import BOOKKEEPING, LIFECYCLE
from niadra.domain.objects.undeclared import as_value, held, humanized, says_person
from niadra.domain.objects.written import distinctive

MAX_TYPES = 50
MAX_FIELDS = 100
MAX_STATES = 50
OPERATION_EVENTS = 2
"""An operation is the space's once two system events confirmed it: one malformed event names none (P4)."""
OPERATION_KEY = "operation"
CODES_KEPT = 20
"""Per field, the distinct codes the evidence keeps ("business_days", "human_edit"): what a line would say
raw, and the space's words for them are written once (`app.observed_words`)."""
CODE_SEEN = 2
"""A code is worded once two of its field's values carried it (P4)."""
CODE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
"""A machine's token: lowercase ASCII words joined by underscores, never a sentence or a person's name."""
CODE_CHARS = 40
RELATION_MATCHES = 3
"""A field names another type's objects after this many matches with no match of a third type (P4)."""
DUE_VALUES = 3
IDENTIFIER_VALUES = 3
"""A date field is a due date once this many of its values were seen, most of them ahead of their event."""
FINAL_OBJECTS = 5
FINAL_AFTER = timedelta(days=14)
CURRENT_KEPT = 200
"""Per type, the latest objects whose current state the evidence keeps, to tell a final state: enough to see
`FINAL_OBJECTS` that stayed, small enough to keep the evidence a document."""
HOLD_RULES = "2"
"""The version of `domain.objects.undeclared.held` the evidence's held counts were made by: bumped whenever
the rule changes, so a field it no longer holds is counted afresh."""
NOTICE_BEFORE = "48h"
DIGEST_PREFIX = "h:"
LABEL_CHARS = 60
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SAFE_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SYSTEM = re.compile(r"[^a-z0-9_.:-]+")
DUE_WORDS = frozenset(
    {
        "due",
        "deadline",
        "vence",
        "vencimento",
        "prazo",
        "expires",
        "expiry",
        "until",
        "fim",
        "limite",
        "termino",
    }
)
"""Words a date field's name is made of that say it is when something is due, in EN, PT and ES."""
KINDS = ("datetime", "date", "number", "bool", "string")
"""The kinds a value may show, and the field type each becomes."""


@dataclass(frozen=True, slots=True)
class Shape:
    """One change of one object, as the inferrer reads it: never kept, only counted."""

    type: str
    external_id: str
    state: Mapping[str, Any]
    as_of: datetime
    owned: bool
    system: str | None


def kind_of(value: Any) -> str | None:
    """The kind a value shows; None for what a line never says as a field (an empty value, a list, a map)."""
    if value is None or isinstance(value, (list, dict)):
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        if not value.strip():
            return None
        typed = as_value(value)
        if isinstance(typed, datetime):
            return "datetime"
        if isinstance(typed, date):
            return "date"
        return "string"
    return None


def empty() -> dict[str, Any]:
    return {"seq": 0, "types": {}, "limited": {}, "operations": {}, "hold_rules": HOLD_RULES}


def seen(
    evidence: Mapping[str, Any], shapes: Iterable[Shape], named: Mapping[str, Iterable[str]]
) -> dict[str, Any]:
    """The evidence after these changes. `named` maps a field value to the types of the space's objects whose
    key is that value (what a relation is found by)."""
    out: dict[str, Any] = {
        "seq": evidence.get("seq", 0),
        "types": {name: _copy(t) for name, t in (evidence.get("types") or {}).items()},
        "limited": dict(evidence.get("limited") or {}),
        "operations": dict(evidence.get("operations") or {}),
        "hold_rules": HOLD_RULES,
    }
    if evidence.get("hold_rules") != HOLD_RULES:
        # A field counted held by rules that changed since is counted again by the current ones: the lawyer's
        # name held before staff names were served kept the field unworded for good (juridico-zero, 08/10).
        for entry in out["types"].values():
            for spec in entry["fields"].values():
                spec["held"] = 0
    types: dict[str, Any] = out["types"]
    for shape in shapes:
        _operation(out, shape.state.get(OPERATION_KEY))
        if not SAFE_TYPE.match(shape.type):
            _count(out["limited"], "unsafe_types")
            continue
        entry = types.get(shape.type)
        if entry is None:
            if len(types) >= MAX_TYPES:
                _count(out["limited"], "types")
                continue
            entry = types[shape.type] = {
                "system": shape.system,
                "objects": 0,
                "owned": 0,
                "fields": {},
                "states": {},
                "relations": {},
                "current": {},
                "unsafe": 0,
                "limited": 0,
            }
        entry["objects"] += 1
        entry["owned"] += 1 if shape.owned else 0
        if entry.get("system") is None and shape.system:
            entry["system"] = shape.system
        for name, value in shape.state.items():
            if name == LIFECYCLE:
                _state(entry, shape.external_id, value, shape.as_of)
                continue
            if name in BOOKKEEPING or name.startswith("_"):
                continue
            if not SAFE_NAME.match(name):
                entry["unsafe"] += 1
                continue
            try:
                _field(entry, name, value, shape.as_of, named)
            except (TypeError, ValueError, OverflowError):
                # One value nothing here reads must never stop a space's inference: the run would raise on
                # the same batch for ever (juridico-zero, 07/10/2026: 55 dead tasks). It is counted, as a
                # limit is, and the Console says so.
                _count(out["limited"], "unreadable_values")
    # Confirmed operations past the cap stay counted and are not offered: the Console says how many.
    over = len(confirmed_operations(out)) - MAX_OPERATIONS
    if over > 0:
        out["limited"]["operations_not_offered"] = over
    else:
        out["limited"].pop("operations_not_offered", None)
    return out


def observed_operations(evidence: Mapping[str, Any]) -> list[str]:
    """The operations the systems confirmed at least `OPERATION_EVENTS` times, most seen first, up to
    `MAX_OPERATIONS`; how many more were confirmed is the evidence's `operations_not_offered`."""
    return confirmed_operations(evidence)[:MAX_OPERATIONS]


def confirmed_operations(evidence: Mapping[str, Any]) -> list[str]:
    counted: Mapping[str, int] = evidence.get("operations") or {}
    return [name for _, name in sorted((-n, name) for name, n in counted.items() if n >= OPERATION_EVENTS)]


def wordable(evidence: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """What a line would say raw from the systems' traffic: the field names never held, and the states and
    codes seen `CODE_SEEN` times. Sorted, so the same evidence asks for the same words."""
    names: set[str] = set()
    codes: set[str] = set()
    for entry in (evidence.get("types") or {}).values():
        codes.update(s for s in entry.get("states") or {} if CODE.match(s) and len(s) <= CODE_CHARS)
        for name, spec in (entry.get("fields") or {}).items():
            if spec.get("held"):
                continue
            names.add(name)
            # A lowercase token of a field about a person ("login": "maria_silva") reads as a code: it is a
            # customer's value, and only names and machines' codes go to the model that words them.
            if not says_person(name):
                codes.update(c for c, n in (spec.get("codes") or {}).items() if n >= CODE_SEEN)
    return sorted(names), sorted(codes)


def _named(entry: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """A type's field names that are never held and its states that read as codes, sorted."""
    fields = sorted(n for n, spec in (entry.get("fields") or {}).items() if not spec.get("held"))
    states = sorted(s for s in entry.get("states") or {} if CODE.match(s) and len(s) <= CODE_CHARS)
    return fields, states


def _codes_of(entry: Mapping[str, Any]) -> dict[str, list[str]]:
    """The codes seen `CODE_SEEN` times in each of a type's fields, as `wordable` takes them."""
    found: dict[str, list[str]] = {}
    for name, spec in (entry.get("fields") or {}).items():
        if spec.get("held") or says_person(name):
            continue
        if codes := sorted(c for c, n in (spec.get("codes") or {}).items() if n >= CODE_SEEN):
            found[name] = codes
    return found


def word_context(
    evidence: Mapping[str, Any], names: Collection[str], codes: Collection[str], *, types: int, per_type: int
) -> list[dict[str, Any]]:
    """The record types `names` and `codes` come from, by names alone: each with its field names, its states
    and its fields' codes, so the model that words them reads "held" as a state of a record and
    "claim_amount" as a field of its type (`domain.objects.words`). No value of a customer: a code of a field
    about a person is never one (`wordable`). At most `types` types and `per_type` names of each."""
    wanted_names, wanted_codes = set(names), set(codes)
    found: list[dict[str, Any]] = []
    for type_name, entry in sorted((evidence.get("types") or {}).items()):
        fields, states = _named(entry)
        coded = _codes_of(entry)
        if not (
            wanted_names & set(fields)
            or wanted_codes & set(states)
            or any(wanted_codes & set(c) for c in coded.values())
        ):
            continue
        one: dict[str, Any] = {"type": type_name, "fields": fields[:per_type]}
        if states:
            one["states"] = states[:per_type]
        if coded:
            one["codes"] = {n: c[:per_type] for n, c in sorted(coded.items())[:per_type]}
        found.append(one)
        if len(found) >= types:
            break
    return found


def shared_wordable(evidence: Mapping[str, Any]) -> dict[str, tuple[list[str], list[str]]]:
    """Per record type, the field names and codes (`wordable`'s) it shares with another type: where one word
    for the space cannot say both senses, a hearing `held` (took place) and a fee `held` (kept back). Sorted,
    so the same evidence asks for the same words."""
    per_type: dict[str, tuple[set[str], set[str]]] = {}
    for type_name, entry in (evidence.get("types") or {}).items():
        fields, states = _named(entry)
        codes = set(states) | {c for found in _codes_of(entry).values() for c in found}
        per_type[type_name] = (set(fields), codes)
    seen_names: dict[str, int] = {}
    seen_codes: dict[str, int] = {}
    for names, codes in per_type.values():
        for name in names:
            seen_names[name] = seen_names.get(name, 0) + 1
        for code in codes:
            seen_codes[code] = seen_codes.get(code, 0) + 1
    shared: dict[str, tuple[list[str], list[str]]] = {}
    for type_name, (names, codes) in sorted(per_type.items()):
        own_names = sorted(n for n in names if seen_names[n] > 1)
        own_codes = sorted(c for c in codes if seen_codes[c] > 1)
        if own_names or own_codes:
            shared[type_name] = (own_names, own_codes)
    return shared


def one_type(evidence: Mapping[str, Any], type_name: str) -> dict[str, Any]:
    """The evidence of `type_name` alone: the context its own words are asked with."""
    entry = (evidence.get("types") or {}).get(type_name)
    return {"types": {type_name: entry}} if entry is not None else {"types": {}}


def word_basis(evidence: Mapping[str, Any]) -> dict[str, int]:
    """How much each name and code was seen with: the most names and states of a record type it belongs
    to, keyed `f:<name>` and `v:<code>` as the stored words' marks are. A word asked with less is asked
    once more when its type has grown (`REWORD_GROWTH`)."""
    basis: dict[str, int] = {}
    for entry in (evidence.get("types") or {}).values():
        fields, states = _named(entry)
        size = len(fields) + len(states)
        keys = [f"f:{n}" for n in fields] + [f"v:{c}" for c in states]
        keys += [f"v:{c}" for codes in _codes_of(entry).values() for c in codes]
        for key in keys:
            basis[key] = max(basis.get(key, 0), size)
    return basis


def observed_types(
    evidence: Mapping[str, Any],
    declared: Collection[str],
    now: datetime,
) -> list[dict[str, Any]]:
    """The observed types, as declarations of the types the space did not declare (`declared` names the ones
    it did, which stay as they are)."""
    found: list[dict[str, Any]] = []
    types: Mapping[str, Any] = evidence.get("types") or {}
    known = set(declared) | set(types)
    for name in sorted(types):
        if name in declared:
            continue
        entry = types[name]
        doc: dict[str, Any] = {
            "type": name,
            "ownership": "subject" if entry["owned"] * 2 >= entry["objects"] else "shared",
            "mirror_of": {"system": _system(entry.get("system")), "derived_by": "observation"},
        }
        states = [s for s in entry["states"] if SAFE_NAME.match(s)][:MAX_STATES]
        if states:
            doc["states"] = states
            stayed = _stayed(entry, now)
            final = [s for s in states if stayed.get(s, 0) >= FINAL_OBJECTS]
            if final:
                doc["lifecycle"] = {"outcome": {"final": final}}
        relations = _relations(entry, known - {name})
        if relations:
            doc["relations"] = relations
        fields: dict[str, dict[str, Any]] = {
            name: {"type": "string", "label": humanized(name)[:LABEL_CHARS]}
            for name in _identifiers(entry, {r["via"] for r in relations.values()})
        }
        due = _due(entry)
        if due is not None:
            # Unlabelled: a line says it as the object's due date in the space's words ("vence em").
            label = {} if "due" in due.split("_") else {"label": humanized(due)[:LABEL_CHARS]}
            fields[due] = {"type": _kind(entry["fields"][due]), **label}
            doc["timers"] = {"notice": {"due": f"{due} - {NOTICE_BEFORE}", "on_fire": ["notify"]}}
        if fields:
            doc["fields"] = fields
        found.append(doc)
    return found


def changes(before: Iterable[Mapping[str, Any]], after: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What changed between two observed registries, as notes a person reads: names and kinds, never a
    value."""
    old = {t["type"]: t for t in before}
    notes: list[dict[str, Any]] = []
    for t in after:
        name = t["type"]
        was = old.get(name, {})
        if not was and "ownership" in t:
            notes.append({"kind": "type", "type": name})
        for field, spec in t.get("fields", {}).items():
            if field not in was.get("fields", {}):
                notes.append({"kind": "field", "type": name, "field": field, "as": spec["type"]})
        for state in t.get("states", []):
            if state not in was.get("states", []):
                notes.append({"kind": "state", "type": name, "state": state})
        for relation, spec in t.get("relations", {}).items():
            if relation not in was.get("relations", {}):
                notes.append({"kind": "relation", "type": name, "to": spec["type"], "via": spec["via"]})
        if "timers" in t and "timers" not in was:
            notes.append({"kind": "due", "type": name, "field": t["timers"]["notice"]["due"].split(" ")[0]})
    return notes


def _copy(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **entry,
        "fields": {
            k: {**v, "kinds": dict(v["kinds"]), "codes": dict(v.get("codes") or {})}
            for k, v in entry["fields"].items()
        },
        "states": dict(entry["states"]),
        "relations": {k: dict(v) for k, v in entry["relations"].items()},
        # An id kept before the evidence kept digests becomes one on the next run.
        "current": {_digest(k): list(v) for k, v in (entry.get("current") or {}).items()},
    }


def _operation(out: dict[str, Any], value: Any) -> None:
    named = canonical_operation(value) if isinstance(value, str) else None
    if named is None:
        return
    operations: dict[str, int] = out["operations"]
    if named not in operations and len(operations) >= MAX_OPERATIONS * 2:
        _count(out["limited"], "operations")
        return
    operations[named] = operations.get(named, 0) + 1


def _count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _state(entry: dict[str, Any], external_id: str, value: Any, as_of: datetime) -> None:
    if not isinstance(value, str) or not value:
        return
    states: dict[str, Any] = entry["states"]
    if value not in states and len(states) >= MAX_STATES:
        entry["limited"] += 1
        return
    states[value] = states.get(value, 0) + 1
    current: dict[str, list[str]] = entry.setdefault("current", {})
    key = _digest(external_id)
    was = current.pop(key, None)
    # Re-inserted last: the dict keeps the latest changes, and the oldest go past the cap.
    current[key] = [value, was[1] if was and was[0] == value else as_of.isoformat()]
    while len(current) > CURRENT_KEPT:
        current.pop(next(iter(current)))


def _digest(external_id: str) -> str:
    """What the evidence keeps of an object's id: a digest. The evidence is configuration, which outlives an
    erasure and a space's reset, and a system may key its objects by a person's document or phone."""
    if external_id.startswith(DIGEST_PREFIX):
        return external_id
    return DIGEST_PREFIX + hashlib.sha256(external_id.encode()).hexdigest()[:20]


def _stayed(entry: Mapping[str, Any], now: datetime) -> dict[str, int]:
    """Per state, how many of the kept objects have been in it for `FINAL_AFTER` or longer."""
    stayed: dict[str, int] = {}
    for state, since in (entry.get("current") or {}).values():
        if now - datetime.fromisoformat(since) >= FINAL_AFTER:
            stayed[state] = stayed.get(state, 0) + 1
    return stayed


def _field(
    entry: dict[str, Any], name: str, value: Any, as_of: datetime, named: Mapping[str, Iterable[str]]
) -> None:
    fields: dict[str, Any] = entry["fields"]
    if name not in fields and len(fields) >= MAX_FIELDS:
        entry["limited"] += 1
        return
    kind = kind_of(value)
    if kind is None:
        return
    spec = fields.setdefault(name, {"kinds": {}, "ahead": 0, "held": 0})
    if held(name, value) is not None:
        spec["held"] += 1
        return
    spec["kinds"][kind] = spec["kinds"].get(kind, 0) + 1
    if kind == "string" and distinctive(value):
        spec["distinctive"] = spec.get("distinctive", 0) + 1
    if kind == "string" and len(value) <= CODE_CHARS and CODE.match(value):
        codes: dict[str, int] = spec.setdefault("codes", {})
        if value in codes or len(codes) < CODES_KEPT:
            codes[value] = codes.get(value, 0) + 1
    if kind in ("date", "datetime") and _ahead(as_value(value), as_of):
        spec["ahead"] += 1
    if kind == "string":
        for other in named.get(value, ()):
            relations = entry["relations"].setdefault(name, {})
            relations[other] = relations.get(other, 0) + 1


def _ahead(value: Any, as_of: datetime) -> bool:
    if isinstance(value, datetime):
        # A system often writes an instant with no offset ("2026-10-05T10:00:00"): it is read in the event's
        # zone, never compared bare with an aware one, which raised and stopped the space's inference.
        return (value if value.tzinfo else value.replace(tzinfo=as_of.tzinfo)) > as_of
    if isinstance(value, date):
        return value > as_of.date()
    return False


def _kind(spec: Mapping[str, Any]) -> str | None:
    kinds: Mapping[str, int] = spec["kinds"]
    if not kinds or spec["held"]:
        return None
    best = max(kinds.values())
    winners = [k for k in KINDS if kinds.get(k) == best]
    # A value of another kind than most (a number sent once as text) leaves the field a text until most of
    # its values agree (P4): text holds every kind, a wrong number type would drop values.
    if len(winners) > 1 or (len(kinds) > 1 and best < 3):
        return "string"
    return winners[0]


def _relations(entry: Mapping[str, Any], others: set[str]) -> dict[str, Any]:
    relations: dict[str, Any] = {}
    for field, targets in entry["relations"].items():
        spec = entry["fields"].get(field)
        if spec is None or _kind(spec) != "string" or len(targets) != 1:
            continue
        ((target, matches),) = targets.items()
        if matches < RELATION_MATCHES or target not in others or target in relations:
            continue
        relations[target] = {"type": target, "via": field}
    return relations


def _identifiers(entry: Mapping[str, Any], relations: set[str]) -> list[str]:
    return sorted(
        name
        for name, spec in entry["fields"].items()
        if name not in relations
        and _kind(spec) == "string"
        and spec["kinds"].get("string", 0) >= IDENTIFIER_VALUES
        and spec.get("distinctive", 0) == spec["kinds"].get("string", 0)
    )


def _due(entry: Mapping[str, Any]) -> str | None:
    candidates = []
    for name, spec in entry["fields"].items():
        if _kind(spec) not in ("date", "datetime") or not DUE_WORDS & set(name.split("_")):
            continue
        dated = spec["kinds"].get("date", 0) + spec["kinds"].get("datetime", 0)
        if dated >= DUE_VALUES and spec["ahead"] * 2 > dated:
            candidates.append((-dated, name))
    return min(candidates)[1] if candidates else None


def _system(system: str | None) -> str:
    said = _SYSTEM.sub("-", (system or "").lower()).strip("-")
    if not said or not said[0].isalpha():
        said = f"system-{said}" if said else "system"
    return said[:64]
