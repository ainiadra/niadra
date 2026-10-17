"""The version rule of a push of shared objects (7.7, 24.3.1): the answer is decided against the hot layer and
the consolidator writes with the same rule, so the two never disagree.

The rule is per field and by the source's version: a pushed field replaces the one held only when the push's
version is greater than the version that wrote that field. Merging by it is commutative, associative and
idempotent (a field keeps the greatest version pushed), so the durable row reaches the state the hot layer
served whatever the order and the grouping of the consolidator's statements, and merging the rows it wrote
back into the hot layer moves nothing backwards. A source never reuses a version for other content.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class PushOutcome(StrEnum):
    APPLIED = "applied"
    """At least one field moved forward; the object holds this version of it, or a greater one later."""
    STALE_VERSION = "stale_version"
    """Every field of the item was already written by this version or a greater one."""
    OUT_OF_SET = "out_of_set"
    """The object is not in the working set: nothing is kept (a push never brings an object in)."""


@dataclass(frozen=True, slots=True)
class FieldWrite:
    """One field as a push wrote it: the source's version and what it carries (sealed value and stamps)."""

    version: int
    payload: object


Held = Mapping[str, FieldWrite]


def merge(held: Held, version: int, fields: Mapping[str, object]) -> tuple[dict[str, FieldWrite], bool]:
    """The fields after writing `fields` at `version`, and whether any of them moved."""
    merged = dict(held)
    moved = False
    for name, payload in fields.items():
        current = merged.get(name)
        if current is None or version > current.version:
            merged[name] = FieldWrite(version, payload)
            moved = True
    return merged, moved


def join(a: Held, b: Held) -> dict[str, FieldWrite]:
    """Two holdings of the same object merged field by field: what the consolidator's statement computes, and
    what the hot layer takes back from the rows it wrote."""
    merged = dict(a)
    for name, write in b.items():
        current = merged.get(name)
        if current is None or write.version > current.version:
            merged[name] = write
    return merged


def decide(held: Held | None, version: int, fields: Mapping[str, object]) -> tuple[PushOutcome, Held | None]:
    """The answer to one item of a push against the hot layer's holding of its object (`None` when the object
    is not in the working set), and the holding afterwards."""
    if held is None:
        return PushOutcome.OUT_OF_SET, None
    merged, moved = merge(held, version, fields)
    return (PushOutcome.APPLIED, merged) if moved else (PushOutcome.STALE_VERSION, held)
