"""How much of each declared field the objects of a type carry, and how old it is (24.2.1): the Console's view
of a type next to its declaration, from the stamps alone (`field_meta`), never a value. Pure."""

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

META_KEY = "$"


@dataclass(frozen=True, slots=True)
class FieldShare:
    field: str
    coverage: float
    """The share of the type's objects some source observed the field of, from 0 to 1."""
    age_p50_s: int | None
    """The median age of the newest observation of the field, among the objects that carry it."""


@dataclass(frozen=True, slots=True)
class TypeShare:
    type: str
    objects: int
    fields: tuple[FieldShare, ...]


def coverage(
    declared: Mapping[str, Sequence[str]], stamps: Iterable[tuple[str, Mapping[str, Any]]], now: datetime
) -> list[TypeShare]:
    """`declared` is each type's fields, in the declaration's order; `stamps` the objects' `{field: {source:
    stamps}}`. A declared field no object carries has a coverage of 0."""
    counts: dict[str, int] = dict.fromkeys(declared, 0)
    ages: dict[tuple[str, str], list[float]] = {}
    for kind, meta in stamps:
        if kind not in counts:
            continue
        counts[kind] += 1
        for field, by in meta.items():
            if field == META_KEY or not isinstance(by, Mapping):
                continue
            seen = [datetime.fromisoformat(s["observed_at"]) for s in by.values() if "observed_at" in s]
            if seen:
                ages.setdefault((kind, field), []).append((now - max(seen)).total_seconds())
    shares = []
    for kind, fields in declared.items():
        total = counts[kind]
        shares.append(
            TypeShare(
                kind,
                total,
                tuple(
                    FieldShare(
                        field,
                        round(len(ages.get((kind, field), ())) / total, 4) if total else 0.0,
                        int(statistics.median(ages[(kind, field)])) if ages.get((kind, field)) else None,
                    )
                    for field in fields
                ),
            )
        )
    return shares
