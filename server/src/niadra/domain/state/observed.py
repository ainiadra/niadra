"""What an object holds of each field: the latest observation of every source that reported it (24.3.1).

An observation keeps its value and stamps, never an expiry: when the value became true in the world
(`valid_at`), when its source observed it, when Niadra first knew of it (`known_at`), the source's version,
the observer and the provenance. A report moves a source's field only forward in the world's time: a late
one enters the object's history and never moves a field back. Which source a read serves, and how fresh it
is, is decided when it is read, against the rules in force (L19).
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import orjson


@dataclass(frozen=True, slots=True)
class Previous:
    """A field's value before its latest change, kept for the fields that track changes."""

    value: Any


@dataclass(frozen=True, slots=True)
class Observation:
    value: Any
    valid_at: datetime
    observed_at: datetime
    known_at: datetime
    version: str | None = None
    observer: str | None = None
    provenance: str | None = None
    """`live`, `snapshot` or `cache` when a tool reported it; None when the source reported it itself."""
    was: Previous | None = None


Holding = Mapping[str, Mapping[str, Observation]]
"""Field -> source -> the source's latest observation of the field."""


@dataclass(frozen=True, slots=True)
class Report:
    """What one source says of an object at once: a system event, a push, a tool's observation."""

    source: str
    fields: Mapping[str, Any]
    valid_at: datetime
    observed_at: datetime
    received_at: datetime
    version: str | None = None
    observer: str | None = None
    provenance: str | None = None


def merge(
    holding: Holding, report: Report, *, revisions: bool = False, tracked: Collection[str] = ()
) -> tuple[dict[str, dict[str, Observation]], frozenset[str]]:
    """The holding after `report`, and the fields it moved.

    A report moves a source's field when it is newer in the world (`valid_at`); at the same instant, when its
    version is greater, or, for a type that allows revisions, when it came later. A move at the same instant,
    or any move of a type that allows revisions, is a revision of the same observation: it keeps the time
    Niadra first knew of it, so what is new stays new. `tracked` fields keep the value a move replaced."""
    merged = {name: dict(sources) for name, sources in holding.items()}
    moved: set[str] = set()
    for name, value in report.fields.items():
        current = merged.get(name, {}).get(report.source)
        if current is not None and not _moves(current, report, revisions):
            continue
        observation = Observation(
            value,
            report.valid_at,
            report.observed_at,
            report.received_at,
            report.version,
            report.observer,
            report.provenance,
        )
        if current is not None:
            revised = revisions or report.valid_at == current.valid_at
            was = Previous(current.value) if name in tracked and current.value != value else current.was
            observation = replace(
                observation, known_at=current.known_at if revised else report.received_at, was=was
            )
        merged.setdefault(name, {})[report.source] = observation
        moved.add(name)
    return merged, frozenset(moved)


def _moves(current: Observation, report: Report, revisions: bool) -> bool:
    if report.valid_at != current.valid_at:
        return report.valid_at > current.valid_at
    order = compare_versions(report.version, current.version)
    return order > 0 or (order == 0 and revisions)


def compare_versions(a: str | None, b: str | None) -> int:
    """A source's versions: numbers compare as numbers, anything else as text, and a version beats none."""
    if a == b:
        return 0
    if a is None or b is None:
        return 1 if b is None else -1
    if a.isdigit() and b.isdigit():
        return (int(a) > int(b)) - (int(a) < int(b))
    return (a > b) - (a < b)


def latest(holding: Holding) -> dict[str, Any]:
    """Each field's value as the newest observation in the world has it, whatever its source: the state of an
    object whose type the space does not declare."""
    return {
        name: max(sources.items(), key=lambda item: (item[1].valid_at, item[1].known_at, item[0]))[1].value
        for name, sources in sorted(holding.items())
        if sources
    }


def value_part(o: Observation) -> dict[str, Any]:
    """What of an observation is sealed: the value, and the value it replaced."""
    return {"v": o.value} | ({"w": [o.was.value]} if o.was is not None else {})


def named_text(state: Mapping[str, Any], holding: Holding) -> bytes:
    """The values an object's sealed state writes, what it resolves to and what each source observed, as the
    index of mentions reads them (`domain.privacy.mentions`)."""
    values = {name: {src: value_part(o) for src, o in by.items()} for name, by in holding.items()}
    return orjson.dumps({"s": state, "f": values}, default=str)


def stamps_part(o: Observation) -> dict[str, Any]:
    """What of an observation is stored in the clear: when and by whom, never a value."""
    stamps: dict[str, Any] = {
        "valid_at": o.valid_at.isoformat(),
        "observed_at": o.observed_at.isoformat(),
        "known_at": o.known_at.isoformat(),
    }
    optional = {"version": o.version, "observer": o.observer, "provenance": o.provenance}
    return stamps | {k: v for k, v in optional.items() if v is not None}


def observation_of(value: Mapping[str, Any], stamps: Mapping[str, Any]) -> Observation:
    was = value.get("w")
    return Observation(
        value["v"],
        datetime.fromisoformat(stamps["valid_at"]),
        datetime.fromisoformat(stamps["observed_at"]),
        datetime.fromisoformat(stamps["known_at"]),
        stamps.get("version"),
        stamps.get("observer"),
        stamps.get("provenance"),
        Previous(was[0]) if was else None,
    )


def encode(holding: Holding) -> dict[str, dict[str, dict[str, Any]]]:
    """A holding as one JSON document, values and stamps together (the read path's sealed copy)."""
    return {
        name: {src: value_part(o) | stamps_part(o) for src, o in by.items()} for name, by in holding.items()
    }


def decode(document: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, Observation]]:
    return {name: {src: observation_of(o, o) for src, o in by.items()} for name, by in document.items()}
