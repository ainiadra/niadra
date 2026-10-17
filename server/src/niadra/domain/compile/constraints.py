"""The constraints block of one subject for one read (`constraints.v0`, 24.3.2), from their compact signals
with the session's pending interactions folded in (`domain.signals.derive`).

- `hard` holds only what the person said with `must` or `must_not`: an inference is never hard. A
  preference said for the session holds only in its conversation; a turn's is the SDK's alone.
- `soft` holds what they said with `prefer` or `avoid`, then the values affinity infers.
- `attributes` holds what they said about themselves, applied always; then what their outcomes say (a size
  bought or kept), and the size their own signal points to, both applied only when they ask for their size
  and only where they said nothing. A size returned for not fitting takes the one bought back, and is never
  offered as their size.
- Two hard constraints on the same field that cannot both hold: the later one is kept (`current_utterance`).
  A stated entry and an inferred one that disagree: the stated one is kept (`precedence`). Both stay in the
  block, so the turn record can cite either, and `conflicts` says which one stands.
- The block of a beneficiary holds the beneficiary's signals only; a subject with beneficiaries is asked
  `for_whom` rather than assumed.
- `already_presented` carries, for each item, the numbers it was last shown with (a price, a total, a
  discount, an installment), with their role and whether they may be claimed now: what a claim check takes as
  evidence for an offer the agent quotes back.
- An experiment's control arm reads without what it holds out (`withheld`, 6.4): the inferred soft entries
  (`soft`) or the inferred size (`size`); what the person said always stays.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.domain.coordinate.window import CONTACT_WINDOW_TRAIT, InvalidWindow, Window
from niadra.domain.memory.bound import BoundRow
from niadra.domain.signals.affinity import Affinity, Prior, no_prior, sizes, soft_entries
from niadra.domain.signals.body import Body, Presented, Stated, Trait
from niadra.domain.signals.derive import Catalog, holds, shown_numbers
from niadra.domain.signals.inferences import size_name
from niadra.domain.signals.render import satisfies, version

SAID = ("stated", "correction")
DERIVED = {"kept": 0.9, "acquired": 0.7}
"""What an outcome says of the subject's size, with how far it goes: a size kept past the exchange window
more than one only bought. `returned_for_size` says a size did not fit, and is never offered."""
HARD = ("must", "must_not")
_NEGATION = {"in": "not_in", "not_in": "in", "eq": "ne", "ne": "eq"}
_NEGATION |= {"lt": "gte", "gte": "lt", "lte": "gt", "gt": "lte"}
_POSITIVE = ("in", "eq")
_NEGATIVE = ("not_in", "ne")
MAX_HARD = 100
"""Hard entries a block carries, the contract's and the SDKs' size: past it the restrictions (`must_not`)
keep their room before the requirements, the newest first, and the rest are counted (`omitted`)."""
MAX_SOFT = 100
MAX_LIST = 200
MAX_INSTRUCTIONS = 500
"""A runaway guard on the instructions a block carries: every one below it, the restrictions (`must_not`,
one that holds with one person) before the rest, the newest first; past it the rest are counted. The 50
newest it once kept could cut the restriction that kept a client's wife from his case amounts."""


def empty(for_: str) -> dict[str, Any]:
    """The block of someone with no signals: still versioned, so a turn record can cite it."""
    block: dict[str, Any] = {"subject": {"for": for_}}
    block["version"] = version(block)
    return block


def build(
    body: Body | None,
    *,
    for_: str,
    session: str | None,
    now: datetime,
    catalog: Catalog,
    prior: Prior = no_prior,
    others: bool = False,
    withheld: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """The block as the constraints spec writes it, `version` included. `others` says the subject has
    beneficiaries or gifts of their own."""
    if body is None:
        nothing = empty(for_)
        if others and for_ == "self":
            nothing["ask"] = ["for_whom"]
            nothing["version"] = version(nothing)
        return nothing
    said = sorted((s for s in body.stated if holds(s, now, session)), key=lambda s: (s.said_at, s.key))
    omitted: dict[str, int] = {}
    hard: list[dict[str, Any]] = []
    hard_said: list[tuple[str, Stated, str, list[Any]]] = []
    stated_hard = [s for s in said if s.strength in HARD]
    if len(stated_hard) > MAX_HARD:
        # The restrictions keep their room before the requirements, the newest first: a block cut in silence
        # once kept the oldest hundred.
        room = sorted(stated_hard, key=lambda s: (s.strength != "must_not", -s.said_at.timestamp(), s.key))
        keep = {s.key for s in room[:MAX_HARD]}
        omitted["hard"] = len(stated_hard) - MAX_HARD
        said = [s for s in said if s.strength not in HARD or s.key in keep]
    for s in said:
        if s.strength not in HARD:
            continue
        op = s.op if s.strength == "must" else _NEGATION.get(s.op)
        if op is None:
            continue  # "not between" has no single operator: the SDK keeps it for its own turn
        entry_id = f"h{len(hard) + 1}"
        hard.append(
            {
                "id": entry_id, "attr": s.attr, "op": op, "values": list(s.values), "source": s.source,
                "scope": s.scope, "category": s.category, "relax": "never",
                "origin": {"kind": s.source, "event_key": s.event_key, "turn_id": s.turn_id},
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            }
        )  # fmt: skip
        hard_said.append((entry_id, s, op, list(s.values)))
    soft: list[dict[str, Any]] = []
    soft_polarity: dict[str, tuple[str, str, Any, str]] = {}
    left = 0
    for s in said:
        if s.strength in HARD or s.op not in _POSITIVE + _NEGATIVE:
            continue
        polarity = "prefer" if (s.strength == "prefer") == (s.op in _POSITIVE) else "avoid"
        for value in s.values:
            if len(soft) < MAX_SOFT:
                entry_id = f"s{len(soft) + 1}"
                soft.append(_soft(entry_id, s.attr, value, polarity, 1.0, 1.0, "stated", s.category))
                soft_polarity[entry_id] = (s.attr, polarity, value, "stated")
            else:
                left += 1
    inferred = [] if "soft" in withheld else soft_entries(body.values.values(), catalog.family, prior)
    for a in inferred:
        if len(soft) < MAX_SOFT:
            entry_id = f"s{len(soft) + 1}"
            soft.append(
                _soft(entry_id, a.attr, a.value, a.polarity, a.weight, a.confidence, "inferred", None)
            )
            soft_polarity[entry_id] = (a.attr, a.polarity, a.value, "inferred")
        else:
            left += 1
    if left:
        omitted["soft"] = left
    own = [t for t in body.traits if not t.name.startswith(CONTACT_WINDOW_TRAIT)]
    attributes = [
        {
            "id": f"z{i + 1}", "name": t.name, "system": t.system, "value": t.value, "confidence": 1.0,
            "source": t.source, "apply": "always", "category": t.category,
        }
        for i, t in enumerate(
            sorted((t for t in own if t.source in SAID), key=lambda t: (t.name, t.category or "", t.said_at))
        )
    ]  # fmt: skip
    said_names = {a["name"] for a in attributes}
    for t in sorted(own, key=lambda t: (t.name, t.category or "", t.said_at)):
        if t.source in DERIVED and t.name not in said_names and "size" not in withheld:
            attributes.append(
                {
                    "id": f"z{len(attributes) + 1}", "name": t.name, "system": t.system, "value": t.value,
                    "confidence": DERIVED[t.source], "source": t.source, "apply": "when_asked",
                    "category": t.category,
                }
            )  # fmt: skip
    named = {a["name"] for a in attributes}
    for a in [] if "size" in withheld else sizes(body.values.values(), catalog.family, prior):
        entry = _size(f"z{len(attributes) + 1}", a, catalog)
        if entry["name"] not in named:
            attributes.append(entry)
    numbers = shown_numbers(body, catalog, now)
    conflicts = _hard_conflicts(hard_said) + _soft_conflicts(hard_said, soft_polarity)
    relaxable = [a["id"] for a in attributes if a["source"] not in SAID]
    for name, listed in (("exclude", body.excluded), ("already_presented", body.presented)):
        if len(listed) > MAX_LIST:
            omitted[name] = len(listed) - MAX_LIST
    block: dict[str, Any] = {
        "subject": {"for": for_},
        "hard": hard,
        "soft": soft,
        "attributes": attributes,
        "exclude": [r for r, _ in sorted(body.excluded.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)][
            :MAX_LIST
        ],
        "already_presented": [
            _shown(p, numbers.get(p.ref))
            for p in sorted(body.presented.values(), key=lambda p: (p.at, p.ref), reverse=True)[:MAX_LIST]
        ],
        "relaxation_order": (["soft"] if soft else []) + relaxable,
        "conflicts": conflicts,
        "ask": ["for_whom"] if others and for_ == "self" else [],
        "contact_windows": contact_windows(body.traits),
        "omitted": omitted or None,
    }
    block = _compact(block)
    block["version"] = version(block)
    return block


def contact_windows(traits: Sequence[Trait]) -> list[dict[str, Any]]:
    """The contact windows the subject said (`contact_window.<channel or any>`), the latest per channel; an
    empty one was lifted. One whose stored window no longer reads is left out, never guessed."""
    found: list[dict[str, Any]] = []
    for t in sorted(traits, key=lambda t: t.name):
        if not t.name.startswith(CONTACT_WINDOW_TRAIT) or t.source not in SAID or not t.value:
            continue
        try:
            window = Window.decode(str(t.value))
        except InvalidWindow:
            continue
        channel = t.name.removeprefix(CONTACT_WINDOW_TRAIT)
        found.append(
            {
                "channel": None if channel == "any" else channel,
                "window": window.to_json(),
                "said_at": t.said_at.isoformat(),
            }
        )
    return found


def gated(block: dict[str, Any], keeps: Callable[[str], bool]) -> dict[str, Any]:
    """The block without the entries `keeps` refuses, versioned again. `keeps` is asked about what each
    entry names: a constraint's field (`type.field`), an attribute's name, the type of a ref it excludes or
    says was shown, and the field of each number an item was shown with. A conflict or a relaxation step that
    names a dropped entry leaves with it."""
    kept = dict(block)
    kept["hard"] = [h for h in block.get("hard") or () if keeps(h["attr"])]
    kept["soft"] = [s for s in block.get("soft") or () if keeps(s["attr"])]
    kept["attributes"] = [a for a in block.get("attributes") or () if keeps(a["name"])]
    kept["exclude"] = [r for r in block.get("exclude") or () if keeps(ref_type(r))]
    kept["already_presented"] = [
        _shown_kept(p, keeps) for p in block.get("already_presented") or () if keeps(ref_type(p["ref"]))
    ]
    ids = {e["id"] for key in ("hard", "soft", "attributes") for e in kept[key]}
    kept["conflicts"] = [c for c in block.get("conflicts") or () if set(c["ids"]) <= ids]
    kept["relaxation_order"] = [r for r in block.get("relaxation_order") or () if r in ids or r == "soft"]
    if not kept["soft"]:
        kept["relaxation_order"] = [r for r in kept["relaxation_order"] if r != "soft"]
    kept.pop("version", None)
    kept = _compact(kept)
    kept["version"] = version(kept)
    return kept


def with_instructions(
    block: dict[str, Any],
    focused: Sequence[tuple[str, BoundRow]],
    accounts: Sequence[tuple[UUID, BoundRow]] = (),
) -> dict[str, Any]:
    """The block with what people said must or must not be done about the objects the read is about, and
    with the organizations it is about (`accounts`, by profile), naming no object: hard
    entries that never relax, each one in `instructions`, and one about a field of the object's type also in
    `hard`, with `scope: object`, where a tool binding applies it. Versioned again."""
    if not focused and not accounts:
        return block
    out = {k: v for k, v in block.items() if k != "version"}
    hard = list(out.get("hard") or [])
    entries: list[dict[str, Any]] = []
    targets: list[tuple[str | None, str | None, BoundRow]] = [
        *((ref, None, row) for ref, row in focused),
        *((None, str(profile), row) for profile, row in accounts),
    ]
    if len(targets) > MAX_INSTRUCTIONS:
        # The restrictions keep their room, the newest first; the rest are counted, never cut in silence.
        room = sorted(targets, key=lambda t: (not _restricts(t[2]), -t[2].said_at.timestamp(), t[2].row_id))
        kept = {id(t) for t in room[:MAX_INSTRUCTIONS]}
        omitted = dict(out.get("omitted") or {})
        omitted["instructions"] = len(targets) - MAX_INSTRUCTIONS
        out["omitted"] = omitted
        targets = [t for t in targets if id(t) in kept]
    # Said oldest first.
    for ref, about, row in sorted(targets, key=lambda t: (t[2].said_at, t[2].row_id)):
        hard_id = None
        if ref is not None and row.attr and len(hard) < MAX_HARD:
            hard_id = f"h{len(hard) + 1}"
            hard.append(
                {
                    "id": hard_id, "attr": row.attr, "op": "eq" if row.strength == "must" else "ne",
                    "values": [True], "source": "stated", "scope": "object", "object": ref, "relax": "never",
                    "origin": {"kind": "stated"},
                    "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                }
            )  # fmt: skip
        entries.append(
            {
                "id": f"i{len(entries) + 1}", "object": ref, "about": about, "text": row.text,
                "strength": row.strength, "attr": row.attr if ref else None, "hard": hard_id,
                "role": row.role, "only_with": row.only_with, "contact": row.contact or None,
                "said_at": row.said_at.isoformat(),
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            }
        )  # fmt: skip
    out["hard"] = hard
    out["instructions"] = entries
    out = _compact(out)
    out["version"] = version(out)
    return out


def _restricts(row: BoundRow) -> bool:
    """Whether an instruction keeps someone from something: a `must_not`, or one held with one person."""
    return row.strength == "must_not" or row.only_with is not None


def _shown(p: Presented, numbers: dict[str, Any] | None) -> dict[str, Any]:
    """An item shown before, with the numbers it was last shown with, when its type declares any."""
    entry: dict[str, Any] = {"ref": p.ref, "times": p.times, "last_at": p.at.isoformat()}
    if numbers:
        entry["values"] = numbers
    return entry


def _shown_kept(entry: dict[str, Any], keeps: Callable[[str], bool]) -> dict[str, Any]:
    """An item shown before, without the numbers of the fields `keeps` refuses."""
    type_ = ref_type(entry["ref"])
    values = {k: v for k, v in (entry.get("values") or {}).items() if keeps(f"{type_}.{k}")}
    kept = {k: v for k, v in entry.items() if k != "values"}
    return {**kept, "values": values} if values else kept


def ref_type(ref: str) -> str:
    """The type of a compact ref (`type:namespace:id`)."""
    return ref.split(":", 1)[0]


def _soft(
    entry_id: str,
    attr: str,
    value: Any,
    polarity: str,
    weight: float,
    confidence: float,
    source: str,
    category: str | None,
) -> dict[str, Any]:
    return {
        "id": entry_id, "attr": attr, "value": value, "polarity": polarity, "weight": round(weight, 4),
        "confidence": round(confidence, 4), "source": source, "category": category,
    }  # fmt: skip


def _size(entry_id: str, a: Affinity, catalog: Catalog) -> dict[str, Any]:
    return {
        "id": entry_id, "name": size_name(a.attr, catalog), "system": None,
        "value": a.value, "confidence": round(a.confidence, 4), "source": "inferred", "apply": "when_asked",
        "category": None,
    }  # fmt: skip


def _clash(op_a: str, values_a: list[Any], op_b: str, values_b: list[Any]) -> bool:
    """Whether no value can honor both constraints. Only set constraints are compared; a range is left to the
    tool, which reports it as a relaxation."""
    if op_a in _POSITIVE and op_b in _POSITIVE:
        return not any(satisfies("in", values_b, v) for v in values_a)
    if op_a in _POSITIVE and op_b in _NEGATIVE:
        return all(satisfies("in", values_b, v) for v in values_a)
    if op_a in _NEGATIVE and op_b in _POSITIVE:
        return all(satisfies("in", values_a, v) for v in values_b)
    return False


def _hard_conflicts(hard: list[tuple[str, Stated, str, list[Any]]]) -> list[dict[str, Any]]:
    out = []
    for i, (id_a, a, op_a, values_a) in enumerate(hard):
        for id_b, b, op_b, values_b in hard[i + 1 :]:
            if a.attr == b.attr and a.category == b.category and _clash(op_a, values_a, op_b, values_b):
                # Sorted by when they were said: the later one is the current utterance.
                out.append({"ids": [id_a, id_b], "kept": id_b, "by": "current_utterance"})
    return out


def _soft_conflicts(
    hard: list[tuple[str, Stated, str, list[Any]]], soft: dict[str, tuple[str, str, Any, str]]
) -> list[dict[str, Any]]:
    out = []
    for soft_id, (attr, polarity, value, source) in soft.items():
        for hard_id, s, op, values in hard:
            if s.attr != attr:
                continue
            allowed = satisfies(op, values, value)
            if (polarity == "prefer" and not allowed) or (
                polarity == "avoid" and op in _POSITIVE and allowed
            ):
                out.append({"ids": [hard_id, soft_id], "kept": hard_id, "by": "precedence"})
        if source != "inferred":
            continue
        for other_id, (other_attr, other_polarity, other_value, other_source) in soft.items():
            if (
                other_source == "stated"
                and other_attr == attr
                and other_value == value
                and other_polarity != polarity
            ):
                out.append({"ids": [other_id, soft_id], "kept": other_id, "by": "precedence"})
    return out


def _compact(block: dict[str, Any]) -> dict[str, Any]:
    """Empty lists and absent values stay out, so the same content always has the same version."""
    return {k: _drop_none(v) for k, v in block.items() if v not in ([], None)}


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value
