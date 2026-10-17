"""What an object keeps about its lifecycle, computed values, timers and inputs besides its fields (24.3.6 to
24.3.8).

Stamps and versions only, never a value: the version each computed value reached and the one it superseded,
where each timer stands, the inputs whose change expired a derived object. They are kept beside the fields'
stamps (`field_meta`), so the sweep and the dependency expiry update them in one statement without opening
a state. The latches (the first time each latching state was reached) name lifecycle states, which are the
object's state, and are sealed with it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ValueVersion:
    """The version a computed value reached (L26): a new value, rule or input hash is a new version."""

    version: int
    rule: str
    inputs_hash: str
    recorded_at: datetime
    supersedes: int | None = None
    digest: str | None = None
    """On a shared object, the space's keyed digest of the value this version holds: a shared object keeps no
    earlier state to compare with, and the same value pushed again is not a revision."""


@dataclass(frozen=True, slots=True)
class TimerMark:
    """Where one timer of the object stands: the firing it is on, when it is due by the values it was armed
    with, and the firing it supersedes after its value was revised."""

    timer_id: UUID
    state: str
    due_at: datetime | None
    value_version: int | None = None
    fired_at: datetime | None = None
    supersedes_firing_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ScanMark:
    """The scan state of the content a field holds (L27), by the content's digest: `pending`, `clean` or
    `flagged`, and the rule or the person that decided it."""

    sha256: str
    state: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Typed:
    latches: Mapping[str, datetime] = field(default_factory=dict)
    values: Mapping[str, ValueVersion] = field(default_factory=dict)
    timers: Mapping[str, TimerMark] = field(default_factory=dict)
    expired_by: tuple[str, ...] = ()
    """The inputs whose change expired a derived object, in the order they changed."""
    expired_at: datetime | None = None
    scans: Mapping[str, ScanMark] = field(default_factory=dict)
    inputs: Mapping[str, str] = field(default_factory=dict)
    """On a shared object, the space's keyed digest of each field a derived type names as an input, as the
    last typed pass saw it: a new digest expires the objects derived from it."""
    bound: str | None = None
    """On a derived shared object, the digest of the inputs it was last bound to: the typed pass binds it
    again only when they change."""
    pieces: str | None = None
    """On a composite shared object, the digest of the related objects its derived fields read, as the last
    typed pass brought them into the working set."""
    matched: tuple[tuple[str, str], ...] = ()
    """On a customer's object, (field, value) of each field a reverse relation finds it by, as the write
    resolved it: the database keeps them only as the space's keyed digests, never in the stamps."""
    erased: frozenset[str] = frozenset()
    """The space's keyed digests (hex) of the identifiers an erasure redacted out of the object (5.14.0): a
    report from the system of record that writes one of them again has it redacted on the way in, so the
    erased person does not come back with the next push. Sealed with the state."""


@dataclass(frozen=True, slots=True)
class NewValue:
    """One version of a computed value, as `object_values` keeps it."""

    name: str
    version: int
    value: Any
    absent_as: str | None
    rule: str
    inputs_hash: bytes
    inputs: tuple[str, ...]
    computed_at: datetime
    valid_until: datetime | None
    declared_gaps: tuple[str, ...]
    gap_effect: str
    supersedes: int | None
    recorded_at: datetime
    digest: str | None = None


@dataclass(frozen=True, slots=True)
class TimerWrite:
    """One row of `object_timers` as a write leaves it."""

    timer_id: UUID
    name: str
    state: str
    due_at: datetime | None
    value_version: int | None
    once_per_key: str | None
    supersedes_firing_id: UUID | None = None
    fired_at: datetime | None = None


UNTYPED = Typed()
"""An object nothing typed was kept for yet."""
META_KEY = "$"
"""The entry of `field_meta` that holds the typed stamps; no field is named so."""
STAMP_SEQ = "n"
"""The member of a shared object's typed stamps that every write of them moves: the hot copy keeps only a
greater one, so stamps written earlier and published later never replace newer ones."""


def meta_part(typed: Typed) -> dict[str, Any]:
    """What `field_meta` keeps: stamps and versions."""
    part: dict[str, Any] = {}
    if typed.values:
        part["v"] = {name: _version(v) for name, v in sorted(typed.values.items())}
    if typed.timers:
        part["t"] = {name: timer_json(t) for name, t in sorted(typed.timers.items())}
    if typed.expired_by:
        part["by"] = list(typed.expired_by)
        part["at"] = typed.expired_at.isoformat() if typed.expired_at else None
    if typed.scans:
        part["s"] = {name: scan_json(s) for name, s in sorted(typed.scans.items())}
    if typed.inputs:
        part["i"] = dict(sorted(typed.inputs.items()))
    if typed.bound:
        part["r"] = typed.bound
    if typed.pieces:
        part["p"] = typed.pieces
    return part


def scan_json(s: ScanMark) -> dict[str, Any]:
    return {"h": s.sha256, "st": s.state} | ({"r": s.reason} if s.reason else {})


def sealed_part(typed: Typed) -> dict[str, Any]:
    """What the sealed state keeps: the latches, which name lifecycle states, and what erasures redacted."""
    part: dict[str, Any] = {}
    if typed.latches:
        part["l"] = {name: at.isoformat() for name, at in sorted(typed.latches.items())}
    if typed.erased:
        part["e"] = sorted(typed.erased)
    return part


def shared_part(typed: Typed) -> dict[str, Any]:
    """What a shared object keeps under `META_KEY`: its stamps and, in the clear, its latches, since a shared
    object holds no customer's data and has no sealed state of its own."""
    return meta_part(typed) | sealed_part(typed)


def shared_typed(part: Mapping[str, Any] | None) -> Typed:
    return typed_of(part, part)


def typed_of(meta: Mapping[str, Any] | None, sealed: Mapping[str, Any] | None) -> Typed:
    meta, sealed = meta or {}, sealed or {}
    expired_at = meta.get("at")
    return Typed(
        latches={name: datetime.fromisoformat(at) for name, at in (sealed.get("l") or {}).items()},
        values={name: _version_of(v) for name, v in (meta.get("v") or {}).items()},
        timers={name: timer_of(t) for name, t in (meta.get("t") or {}).items()},
        expired_by=tuple(meta.get("by") or ()),
        expired_at=datetime.fromisoformat(expired_at) if expired_at else None,
        scans={name: ScanMark(s["h"], s["st"], s.get("r")) for name, s in (meta.get("s") or {}).items()},
        inputs=dict(meta.get("i") or {}),
        bound=meta.get("r"),
        pieces=meta.get("p"),
        erased=frozenset(sealed.get("e") or ()),
    )


def timer_json(t: TimerMark) -> dict[str, Any]:
    body: dict[str, Any] = {"id": str(t.timer_id), "st": t.state, "due": _iso(t.due_at)}
    if t.value_version is not None:
        body["vv"] = t.value_version
    if t.fired_at is not None:
        body["fired"] = t.fired_at.isoformat()
    if t.supersedes_firing_id is not None:
        body["sup"] = str(t.supersedes_firing_id)
    return body


def timer_of(body: Mapping[str, Any]) -> TimerMark:
    return TimerMark(
        timer_id=UUID(body["id"]),
        state=body["st"],
        due_at=_dt(body.get("due")),
        value_version=body.get("vv"),
        fired_at=_dt(body.get("fired")),
        supersedes_firing_id=UUID(body["sup"]) if body.get("sup") else None,
    )


def _version(v: ValueVersion) -> dict[str, Any]:
    body: dict[str, Any] = {"n": v.version, "r": v.rule, "h": v.inputs_hash, "at": v.recorded_at.isoformat()}
    if v.supersedes is not None:
        body["s"] = v.supersedes
    if v.digest is not None:
        body["d"] = v.digest
    return body


def _version_of(body: Mapping[str, Any]) -> ValueVersion:
    return ValueVersion(
        body["n"], body["r"], body["h"], datetime.fromisoformat(body["at"]), body.get("s"), body.get("d")
    )


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at else None


def _dt(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def stamp_seq(part: Mapping[str, Any] | None) -> int:
    """Where a shared object's stored stamps stand: 0 for stamps no write has moved yet."""
    return int((part or {}).get(STAMP_SEQ) or 0)
